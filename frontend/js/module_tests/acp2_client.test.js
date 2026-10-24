/* ACP 2.0 client query modules: science + research (T-15 / C10a).
 *
 * Everything runs in the node vm harness with stub adapters. No network, no
 * fetch: the sandbox `fetch` throws if any module ever reaches for it.
 */
"use strict";

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;

  const DIGEST = (ch) => "sha256:" + ch.repeat(64);
  const PRODUCER = "0123456789abcdef0123456789abcdef01234567";

  function setup() {
    const fetchCalls = [];
    const sandbox = makeSandbox({
      fetch: function () {
        fetchCalls.push(Array.prototype.slice.call(arguments));
        throw new Error("fetch is forbidden in client query modules");
      },
    });
    loadScript("js/client/resource-store.js", sandbox);
    loadScript("js/workbench/contracts/experience_contract.js", sandbox);
    loadScript("js/client/science-query.js", sandbox);
    loadScript("js/client/research-query.js", sandbox);
    return {
      sandbox,
      C: sandbox.window.ACPClient,
      science: sandbox.window.ACPClient.science,
      research: sandbox.window.ACPClient.research,
      contracts: sandbox.window.ACPWorkbench.contracts.experience,
      store: sandbox.window.ACPClient.store,
      fetchCalls,
    };
  }

  function deferred() {
    let resolve, reject;
    const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
    return { promise, resolve, reject };
  }

  function viewBody(overrides) {
    const body = {
      snapshot_revision: 1,
      project_id: "proj-a",
      task_ref: { job_id: "job-1", attempt: 1, task_id: null },
      manifest_generation: 1,
      artifacts: [{
        artifact_id: "art-1",
        role: "optimized_transition_state",
        relative_path: "RESULT/optimized.xyz",
        availability: "readable",
        generation: 1,
        sha256: null,
        size_bytes: 128,
        reason: null,
      }],
      claims: [{
        claim_id: "claim-1",
        statement: "The endo path is lower in free energy.",
        status: "supported",
        evidence_refs: ["ev-1"],
        reason: null,
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

  function preflightBody() {
    return {
      executed: false,
      metering_consumed: false,
      transfers_attempted: false,
      core_hours_reserved: 0,
      plan: { steps: [] },
      estimate: { wall_seconds: 10 },
    };
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
        capability_steps: [],
        initial_inputs: [],
        method: "r2SCAN-3c",
        mapping: "mapping-1",
        charge: 0,
        multiplicity: 1,
        allowed_substitutions: [],
      },
      budget: {
        model_requests: 1, input_tokens: 1, output_tokens: 1, task_refs: 1,
        recipe_steps: 1, qc_invocations: 1, wall_seconds: 1, queue_seconds: 0,
      },
      approval_ref: "approval-1",
      allowed_change_scope: { mode: "none", finite_substitutions: [] },
      round: 1,
      decision: "proposed",
      created_at: "2026-10-22T12:00:00Z",
      human_edits: [],
    };
    return Object.assign(body, overrides || {});
  }

  function recordBody(overrides) {
    const body = {
      research_id: "res-da-1",
      project_id: "proj-a",
      round: 1,
      decision: "accept",
      plan_definition_digest: DIGEST("2"),
      step_identity_refs: [{
        step_index: 0,
        step_identity: "step-0",
        implementation_version: "release-1",
        upstream_digests: [DIGEST("3")],
        materialized_parameters_digest: DIGEST("4"),
      }],
      task_refs: [{
        project_id: "proj-a",
        job_id: "job-1",
        attempt: 1,
        release_id: "release-1",
      }],
      evidence_refs: [{
        evidence_id: "ev-1",
        kind: "result_manifest",
        sha256: DIGEST("5"),
        relative_path: "RESULT/result_manifest.json",
      }],
      missing_validation: [],
      human_edits: [],
      approval_ref: "approval-1",
      created_at: "2026-10-22T12:30:00Z",
      record_revision: 1,
    };
    return Object.assign(body, overrides || {});
  }

  const SCOPE_A = { projectId: "proj-a", permission: "read" };
  const REF = { jobId: "job-1", attempt: 1 };

  await test("science: key includes project + permission + resource identity + generation", () => {
    const { science } = setup();
    const expected = ["science", "proj-a", "write-mode", "view",
      "task/job-1/attempt/2", "7"].map(encodeURIComponent).join(":");
    assertEq(science.keyFor({ projectId: "proj-a", permission: "write-mode" },
      "view", "task/job-1/attempt/2", 7), expected);
    assert(expected.indexOf("proj-a") !== -1, "project in key");
    assert(expected.indexOf("write-mode") !== -1, "permission in key");
    assert(expected.indexOf("job-1") !== -1, "resource identity in key");
    assert(expected.slice(-1) === "7", "generation in key");
  });

  await test("research: key includes project + permission + resource identity + revision", () => {
    const { research } = setup();
    const expected = ["research", "proj-b", "read", "record",
      "res-1", "4"].map(encodeURIComponent).join(":");
    assertEq(research.keyFor({ projectId: "proj-b" }, "record", "res-1", 4), expected);
    assert(expected.indexOf("proj-b") !== -1, "project in key");
    assert(expected.indexOf(":read:") !== -1, "permission in key");
    assert(expected.slice(-1) === "4", "revision in key");
  });

  await test("science: cross-project same resource yields distinct keys and never overwrites", async () => {
    const { science, store } = setup();
    science.init({
      v2: (path) => {
        const project = path.indexOf("proj-a") !== -1 ? "proj-a" : "proj-b";
        return Promise.resolve(viewBody({ project_id: project, snapshot_revision: project === "proj-a" ? 1 : 2 }));
      },
    });
    const a = await science.fetchView(REF, SCOPE_A);
    const b = await science.fetchView(REF, { projectId: "proj-b", permission: "read" });
    const keyA = science.keyFor(SCOPE_A, "view", "task/job-1/attempt/1", 0);
    const keyB = science.keyFor({ projectId: "proj-b", permission: "read" },
      "view", "task/job-1/attempt/1", 0);
    assert(keyA !== keyB, "different projects must not share a store key");
    assertEq(a.project_id, "proj-a");
    assertEq(b.project_id, "proj-b");
    assertEq(store.snapshot(keyA).data.project_id, "proj-a");
    assertEq(store.snapshot(keyB).data.project_id, "proj-b");
  });

  await test("research: cross-project same record id yields distinct keys and never overwrites", async () => {
    const { research, store } = setup();
    research.init({
      v2: (path) => Promise.resolve(recordBody({
        project_id: path.indexOf("proj-a") !== -1 ? "proj-a" : "proj-b",
        task_refs: [{
          project_id: path.indexOf("proj-a") !== -1 ? "proj-a" : "proj-b",
          job_id: "job-1", attempt: 1, release_id: "release-1",
        }],
        research_id: path.indexOf("proj-a") !== -1 ? "res-da-1" : "res-da-1",
      })),
    });
    const a = await research.fetchRecord("res-da-1", SCOPE_A, { revision: 3 });
    const b = await research.fetchRecord("res-da-1",
      { projectId: "proj-b", permission: "read" }, { revision: 3 });
    const keyA = research.keyFor(SCOPE_A, "record", "res-da-1", 3);
    const keyB = research.keyFor({ projectId: "proj-b", permission: "read" },
      "record", "res-da-1", 3);
    assert(keyA !== keyB, "different projects must not share a store key");
    assertEq(a.project_id, "proj-a");
    assertEq(b.project_id, "proj-b");
    assertEq(store.snapshot(keyA).data.project_id, "proj-a");
    assertEq(store.snapshot(keyB).data.project_id, "proj-b");
  });

  await test("science: late older generation is discarded (StaleDiscard), newer snapshot kept", async () => {
    const first = deferred(), second = deferred();
    const { science, store } = setup();
    let call = 0;
    science.init({
      v2: () => { call += 1; return call === 1 ? first.promise : second.promise; },
    });
    const oldOutcome = science.fetchView(REF, SCOPE_A, { generation: 1 })
      .then(() => "resolved", (error) => error.name);
    const fresh = science.fetchView(REF, SCOPE_A, { generation: 2 });
    second.resolve(viewBody({ snapshot_revision: 2 }));
    assertEq((await fresh).snapshot_revision, 2);
    first.resolve(viewBody({ snapshot_revision: 1 }));
    assertEq(await oldOutcome, "StaleDiscard");
    const key = science.keyFor(SCOPE_A, "view", "task/job-1/attempt/1", 2);
    assertEq(store.snapshot(key).data.snapshot_revision, 2);
  });

  await test("research: late older revision is discarded (StaleDiscard), newer snapshot kept", async () => {
    const first = deferred(), second = deferred();
    const { research, store } = setup();
    let call = 0;
    research.init({
      v2: () => { call += 1; return call === 1 ? first.promise : second.promise; },
    });
    const oldOutcome = research.fetchRecord("res-1", SCOPE_A, { revision: 1 })
      .then(() => "resolved", (error) => error.name);
    const fresh = research.fetchRecord("res-1", SCOPE_A, { revision: 2 });
    second.resolve(recordBody({ record_revision: 2, decision: "revise" }));
    assertEq((await fresh).record_revision, 2);
    first.resolve(recordBody({ record_revision: 1, decision: "accept" }));
    assertEq(await oldOutcome, "StaleDiscard");
    const key = research.keyFor(SCOPE_A, "record", "res-1", 2);
    assertEq(store.snapshot(key).data.decision, "revise");
  });

  await test("science: adapter rejection surfaces as an error and is cached as error, never ok", async () => {
    const { science, store } = setup();
    science.init({ v2: () => Promise.reject(new Error("backend exploded")) });
    let caught = null;
    try { await science.fetchCapabilities(SCOPE_A); } catch (error) { caught = error; }
    assert(caught && caught.message === "backend exploded", "rejection must surface");
    const key = science.keyFor(SCOPE_A, "capabilities", "catalog", 0);
    assertEq(store.snapshot(key).status, "error");
    assertEq(store.snapshot(key).data, undefined);
  });

  await test("science: missing adapter rejects instead of silently succeeding", async () => {
    const { science } = setup();
    science.init({});
    let caught = null;
    try { await science.fetchCapabilities(SCOPE_A); } catch (error) { caught = error; }
    assert(caught && /not configured/.test(caught.message), "missing v2 must reject");
  });

  await test("science: preflight passes transport options and proves zero side effects", async () => {
    let captured = null;
    const { science } = setup();
    science.init({
      v2: (path, opts) => { captured = { path, opts }; return Promise.resolve(preflightBody()); },
    });
    const result = await science.preflight(
      { plan_definition_digest: DIGEST("9") }, SCOPE_A, { timeoutMs: 1234, generation: 5 });
    assertEq(captured.path, "/science/preflight?project_id=proj-a");
    assertEq(captured.opts.method, "POST");
    assertEq(captured.opts.timeoutMs, 1234, "transport timeout forwarded");
    assertEq(captured.opts.headers["content-type"], "application/json");
    assertEq(JSON.parse(captured.opts.body).plan_definition_digest, DIGEST("9"));
    assert(captured.opts.signal && typeof captured.opts.signal.aborted === "boolean",
      "store cancellation signal forwarded");
    assertEq(result.executed, false);
    assertEq(result.metering_consumed, false);
  });

  await test("science: a preflight body claiming execution is rejected", async () => {
    const { science } = setup();
    science.init({ v2: () => Promise.resolve(Object.assign(preflightBody(), { executed: true })) });
    let caught = null;
    try { await science.preflight({}, SCOPE_A); } catch (error) { caught = error; }
    assert(caught && /executed/.test(caught.message), "executed=true must fail the contract");
  });

  await test("science: status is scoped to (job, attempt) and rejects a mismatched attempt", async () => {
    let path = null;
    const { science } = setup();
    science.init({
      v1: (url) => {
        path = url;
        return Promise.resolve({ task_ref: { job_id: "job-1", attempt: 2 }, status: "running" });
      },
    });
    let caught = null;
    try { await science.fetchTaskStatus(REF, SCOPE_A); } catch (error) { caught = error; }
    assert(path && path.indexOf("/attempts/1/status") !== -1, "requested attempt in path");
    assert(caught && /does not match/.test(caught.message), "foreign attempt must be rejected");
  });

  await test("science: artifacts expose exactly the four availability states", async () => {
    const { science, contracts } = setup();
    const states = ["unprepared", "preparing", "readable", "failed"];
    science.init({
      v2: () => Promise.resolve(viewBody({
        artifacts: states.map((availability, index) => ({
          artifact_id: "art-" + index,
          role: "role-" + index,
          relative_path: null,
          availability: availability,
          generation: 1,
          sha256: null,
          size_bytes: null,
          reason: availability === "failed" ? "prepare failed" : null,
        })),
      })),
    });
    const artifacts = await science.fetchArtifacts(REF, SCOPE_A);
    assertEq(artifacts.map((item) => item.availability), states);
    assertEq(contracts.normalizeAvailability("bogus"), "failed");
    assertEq(contracts.normalizeAvailability("readable"), "readable");
    const rejected = contracts.validateViewModel(viewBody({
      artifacts: [{
        artifact_id: "art-x", role: "role-x", availability: "ready", generation: 1,
      }],
    }));
    assert(!rejected.ok, "a fifth availability state must be a contract violation");
    assert(rejected.problems.some((item) => item.indexOf("availability") !== -1));
  });

  await test("science: claim states are projected verbatim, never upgraded", async () => {
    const { science } = setup();
    const states = ["supported", "refuted", "inconclusive", "not_evaluated"];
    science.init({
      v2: () => Promise.resolve(viewBody({
        claims: states.map((status, index) => ({
          claim_id: "claim-" + index,
          statement: "statement " + index,
          status: status,
          evidence_refs: ["ev-" + index],
          reason: null,
        })),
      })),
    });
    const claims = await science.fetchClaims(REF, SCOPE_A);
    assertEq(claims.map((item) => item.status), states);
  });

  await test("science: a cross-project view body is rejected instead of cached", async () => {
    const { science, store } = setup();
    science.init({ v2: () => Promise.resolve(viewBody({ project_id: "proj-evil" })) });
    let caught = null;
    try { await science.fetchView(REF, SCOPE_A); } catch (error) { caught = error; }
    assert(caught && /project_id/.test(caught.message), "foreign project must be rejected");
    const key = science.keyFor(SCOPE_A, "view", "task/job-1/attempt/1", 0);
    assertEq(store.snapshot(key).status, "error");
  });

  await test("science + research: modules never call fetch (all reads go through adapters)", async () => {
    const { science, research, fetchCalls } = setup();
    science.init({
      v1: () => Promise.resolve({ task_ref: { job_id: "job-1", attempt: 1 }, status: "running" }),
      v2: (path) => Promise.resolve(path.indexOf("/research/") === 0
        ? recordBody()
        : (path.indexOf("/science/capabilities") === 0
          ? { protocol_version: "2025-06-18", tools: ["acp_capabilities"] }
          : viewBody())),
    });
    research.init({
      v2: (path) => Promise.resolve(path.indexOf("/proposals/") !== -1 ? proposalBody() : recordBody()),
    });
    await science.fetchCapabilities(SCOPE_A);
    await science.fetchTaskStatus(REF, SCOPE_A);
    await science.fetchView(REF, SCOPE_A);
    await research.fetchProposal("prop-da-001", SCOPE_A);
    await research.fetchRecord("res-da-1", SCOPE_A);
    assertEq(fetchCalls.length, 0, "no module may reach the global fetch");
  });

  await test("research: proposal read is validated and summarized read-only", async () => {
    const { research } = setup();
    research.init({ v2: () => Promise.resolve(proposalBody()) });
    const proposal = await research.fetchProposal({ id: "prop-da-001", revision: 3 }, SCOPE_A);
    assertEq(proposal.proposal_id, "prop-da-001");
    const summary = research.summarizeProposal(proposal);
    assertEq(summary.decision, "proposed");
    assertEq(summary.approval_ref, "approval-1");
    assertEq(summary.plan_definition_digest, DIGEST("2"));
  });

  await test("research: record read exposes round/decision/approval and evidence refs", async () => {
    const { research } = setup();
    research.init({ v2: () => Promise.resolve(recordBody()) });
    const record = await research.fetchRecord("res-da-1", SCOPE_A, { revision: 4 });
    assertEq(record.read_only, undefined);
    const round = await research.readRound("res-da-1", SCOPE_A, { revision: 4 });
    assertEq(round.research_id, "res-da-1");
    assertEq(round.round, 1);
    assertEq(round.decision, "accept");
    assertEq(round.approval_ref, "approval-1");
    assertEq(round.evidence_refs.length, 1);
    assertEq(round.evidence_refs[0].evidence_id, "ev-1");
    assertEq(round.evidence_refs[0].kind, "result_manifest");
  });

  await test("research: cross-project TaskRef inside a record is rejected", async () => {
    const { research, contracts } = setup();
    const bad = recordBody({
      task_refs: [{
        project_id: "proj-other", job_id: "job-1", attempt: 1, release_id: "release-1",
      }],
    });
    const check = contracts.validateResearchRecord(bad);
    assert(!check.ok, "cross-project TaskRef must be a contract violation");
    assert(check.problems.some((item) => /cross-project/.test(item)));

    research.init({ v2: () => Promise.resolve(bad) });
    let caught = null;
    try { await research.fetchRecord("res-da-1", SCOPE_A); } catch (error) { caught = error; }
    assert(caught && /cross-project/.test(caught.message), "adapter body must not be cached");
  });

  await test("research: surface is read-only (no mutation entry points)", () => {
    const { research } = setup();
    assertEq(research.readOnly, true);
    ["approve", "decide", "submit", "cancel", "edit", "create", "update", "delete"].forEach((name) => {
      assertEq(typeof research[name], "undefined", name + " must not exist");
    });
  });

  await test("contracts: a DSH chat/session document cannot pose as a ResearchRecord", () => {
    const { contracts } = setup();
    const chat = Object.assign(recordBody(), {
      session_id: "sess-1",
      messages: [{ role: "user", text: "run the calculation" }],
    });
    const check = contracts.validateResearchRecord(chat);
    assert(!check.ok, "session/messages must be rejected");
    assert(check.problems.some((item) => /session_id/.test(item)));
    assert(check.problems.some((item) => /messages/.test(item)));
  });
};
