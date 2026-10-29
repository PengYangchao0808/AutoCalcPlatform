/** F05 product-entry tests: injected fake gateway/transport, no provider or QC. */
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { main, EXIT_OK, CLI_VERSION } from "@rph/cli";
import { P1_TOOL_NAMES, parsePlatformTaskRef } from "@rph/bridge";
import { PRODUCT_TOOL_NAMES, PLANNER_SKILL_VERSION, runDoctor, runFixturePlanner, singlepointPayload,
  type ProductScienceConfig, type PlannerInput } from "@rph/dsh-bundle";
import { ALL_P2_TOOL_NAMES, FAKE_REPORT_DIGEST, FAKE_REPORT_ID, fakeEvidenceView, captureIo, fakeServer, fakeTaskRefPayload, lastJson } from "./m3-02-harness.js";

const digest = (letter: string) => `sha256:${letter.repeat(64)}`;
const body = "2\nH2\nH 0 0 0\nH 0 0 0.74\n";
const sha = `sha256:${createHash("sha256").update(body).digest("hex")}`;
const budget = { model_requests: 4, input_tokens: 5000, output_tokens: 5000,
  task_refs: 1, recipe_steps: 1, qc_invocations: 1, wall_seconds: 600, queue_seconds: 60 };
const science: ProductScienceConfig = {
  science_seed: { method: "r2SCAN-3c", mapping: "identity", charge: 0, multiplicity: 1,
    structure_provenance: [{ input_id: "h2", sha256: sha, format: "xyz", role: "minimum" }], constraints: [] },
  initial_input: { input_id: "h2", sha256: sha, format: "xyz", role: "minimum" },
  planning_budget: budget,
  submission: { workflow: "singlepoint", input: { input_id: "h2", sha256: sha, xyz_text: body },
    method: { method: "r2SCAN-3c", basis: null, charge: 0, multiplicity: 1 },
    resources: { ncores: 1, memory_gb: 1 }, budget: { unit: "task_count", count: 1, timeout_s: 600 } },
};
function setup() {
  const root = mkdtempSync(join(tmpdir(), "rph-product-"));
  const file = join(root, "science.json");
  writeFileSync(file, JSON.stringify(science));
  const config = join(root, "config.yaml");
  writeFileSync(config, "{}\n");
  return { root, file, env: {
    ACP2_CONTROLLER_URL: "http://127.0.0.1:18765", RPH_INTERPRETER: "python3",
    RPH_WORKING_DIRECTORY: root, ACP_RUN_ROOT: root, ACP_CONFIG: config,
    RPH_PROJECT: "proj-1", RPH_PERMISSION_SCOPE: "read,write", RPH_ARTIFACT_EVIDENCE_PATH: root,
    RPH_RESEARCH_WRITER: "platform-research-cas", RPH_MODEL_REQUESTS: "4",
    RPH_MODEL_PROVIDER: "fake-injected-test", RPH_MODEL_ID: "fake-test-model",
  } };
}
const ok = (data: Record<string, unknown>) => ({ isError: false,
  structuredContent: { schema_version: 1, status: "ok", data } });

function gatewayServer(options: { approved?: boolean; clarification?: boolean; evidence?: boolean; writerFails?: boolean; pending?: boolean; unready?: boolean } = {}) {
  let calls = 0;
  let revision = 1;
  let savedDocument: Record<string, unknown> | null = null;
  const server = fakeServer({ negotiated: true, tools: [...P1_TOOL_NAMES, ...ALL_P2_TOOL_NAMES, ...PRODUCT_TOOL_NAMES],
    toolReply(name, args) {
      if (name.endsWith("model_usage_v1")) return ok({ model_requests: calls,
        identity: { research_id: "res-product", session_id: "ses-product" },
        receipt_rows: calls ? [{ receipt_id: "fake-rcpt-1" }] : [], credential: { configured: true } });
      if (name.endsWith("budget_read_v1")) return ok({ source: "platform-receipt",
        used: Object.fromEntries(Object.keys(budget).map((key) => [key, key === "model_requests" ? calls : 0])), receipts: [] });
      if (name.endsWith("model_plan_v1")) {
        calls++;
        const messages = args["messages"] as Array<{ content: string }>;
        const prompt = JSON.parse(messages[1]?.content ?? "{}") as Record<string, unknown>;
        delete prompt["research_id"];
        const plannerInput = { ...prompt, schema: "acp2.skill.planner.input.v1", skill: "planner", skill_version: PLANNER_SKILL_VERSION } as unknown as PlannerInput;
        const fixture = runFixturePlanner(plannerInput);
        const candidate = { ...fixture, science: { ...fixture.science, capability_steps: [
          { step_index: 0, capability: "electronic.energy", version: "1.0.0", parameters: { ncores: 1, memory_gb: 1 } },
        ] } };
        return ok({ output: JSON.stringify(options.clarification ? { status: "clarification", missing: ["basis"], questions: ["confirm basis"] } : candidate),
          usage: null, receipt_id: "fake-rcpt-1" });
      }
      if (name.endsWith("proposal_read_v1") && savedDocument !== null) return ok({ proposal: savedDocument, revision });
      if (name.endsWith("proposal_create_v1") || name.endsWith("checkpoint_v1")) {
        if (name.endsWith("proposal_create_v1")) savedDocument = { ...(args["proposal"] as Record<string, unknown>),
          approval_ref: "ap-product", plan_definition_digest: digest("b"), research_id: "res-product" };
        else {
          const entry = args["entry"] as {kind: string; value: unknown};
          savedDocument = { ...savedDocument, rph_state: { ...(savedDocument?.["rph_state"] as Record<string, unknown> ?? {}), [entry.kind]: entry.value } };
        }
        if (name.endsWith("checkpoint_v1")) { assert.equal(args["expected_revision"], revision); revision++; }
        return ok({ revision, document: savedDocument,
          approval: { approval_id: "ap-product", status: options.approved ? "approved" : "pending" },
          digests: { science_digest: digest("b"), plan_digest: digest("c"), proposal_digest: digest("d") } });
      }
      if (name === "acp_preflight") return ok({ estimate: { feasible: true, plan_digest: digest("c") } });
      if (name.endsWith("approval_query_v1")) return ok({ approvals: [{ approval_ref: "ap-product",
        status: options.approved ? "approved" : "pending", science_digest: digest("b"), plan_digest: digest("c"), proposal_digest: digest("d") }] });
      if (name === "acp_submit") return ok({ task_ref: fakeTaskRefPayload("proj-1"), idempotency_key: args["idempotency_key"] });
      if (name === "acp_status") return ok({ ...fakeTaskRefPayload("proj-1"), state: options.pending ? "RUNNING" : "COMPLETED",
        ...(options.evidence ? { artifacts: [{ kind: "evidence_report", report_id: FAKE_REPORT_ID,
          evidence_id: FAKE_REPORT_ID, sha256: FAKE_REPORT_DIGEST, relative_path: "REPORT/verified.json" }] } : {}) });
      if (name.endsWith("evidence_prepare_v1")) return ok({ schema: "acp2.runtime.evidence-prepare.v1",
        report_ref: { kind: "evidence_report", report_id: FAKE_REPORT_ID, sha256: FAKE_REPORT_DIGEST,
          relative_path: "REPORT/verified.json", artifact_id: "evidence-report", generation: 1 },
        claim_statuses: { "total-electronic-energy": "supported" } });
      if (name.endsWith("evidence_read_v1")) return ok({ ...fakeEvidenceView(
        parsePlatformTaskRef(fakeTaskRefPayload("proj-1"), "proj-1"), "res-product", 0) });
      if (name.endsWith("record_read_v1")) return { isError: true, structuredContent: { schema_version: 1, status: "error", error: {
        code: "resource_not_ready", message: "fake record not yet created", params: {} } } };
      if (name.endsWith("record_create_v1")) return options.writerFails
        ? { isError: true, structuredContent: { schema_version: 1, status: "error", error: {
          code: "revision_conflict", message: "fake CAS conflict", params: { rule: "stale_revision" } } } }
        : ok({ revision: 1 });
      if (name.endsWith("readiness_v1")) return ok({ facts: ["model", "permissions", "software", "storage", "execution"].map((id) => ({
        id, status: options.unready && id === "software" ? "fail" : "ok", hint: "injected fake readiness; no real execution" })) });
      return undefined;
    },
  });
  return server;
}

test("installed default composition reaches model/preflight/approval using only a fake transport seam", async () => {
  const { file, env } = setup();
  const server = gatewayServer();
  const io = captureIo(env);
  const code = await main(["research", "--question", "energy of H2", "--recipe-digest", digest("a"),
    "--science", file, "--research", "stable-product-seed", "--json"], { createTransport: () => server.transport }, io);
  assert.equal(code, EXIT_OK, JSON.stringify(lastJson(io)));
  const result = lastJson(io)["result"] as Record<string, unknown>;
  assert.equal(result["stop_reason"], "approval_rejected");
  const calls = server.sent.filter((frame) => frame["method"] === "tools/call").map((frame) => (frame["params"] as Record<string, unknown>)["name"]);
  assert.ok(calls.includes("acp_research_model_plan_v1"));
  assert.ok(calls.includes("acp_research_proposal_create_v1"));
  assert.ok(calls.includes("acp_preflight"));
  assert.ok(!calls.includes("acp_submit"), "pending approval never dispatches");
});

test("default product submit carries execution digest and persists CAS recovery checkpoints", async () => {
  const { file, env } = setup();
  const server = gatewayServer({ approved: true });
  const io = captureIo(env);
  const code = await main(["research", "--question", "energy", "--recipe-digest", digest("a"),
    "--science", file, "--research", "stable-product-seed", "--json"], { createTransport: () => server.transport }, io);
  assert.equal(code, EXIT_OK, JSON.stringify(lastJson(io)));
  const frames = server.sent.filter((frame) => frame["method"] === "tools/call").map((frame) => frame["params"] as Record<string, unknown>);
  const submitted = frames.find((params) => params["name"] === "acp_submit");
  assert.ok(submitted, JSON.stringify(lastJson(io)));
  const args = submitted["arguments"] as Record<string, unknown>;
  assert.equal(args["plan_digest"], digest("c"));
  assert.equal(args["proposal_digest"], digest("d"));
  assert.ok(typeof args["idempotency_key"] === "string");
  assert.ok(frames.filter((params) => params["name"] === "acp_research_checkpoint_v1").length >= 2);
  // With the platform evidence-prepare tool the product loop closes: the
  // decision is accepted and the record was persisted (asserted above).
  assert.equal((lastJson(io)["result"] as Record<string, unknown>)["stop_reason"], "accepted");
});

test("default doctor reports connected product facts without a model plan", async () => {
  const { env } = setup();
  const server = gatewayServer();
  const io = captureIo(env);
  assert.equal(await main(["doctor", "--connect", "--environment", "acceptance", "--json"],
    { createTransport: () => server.transport }, io), EXIT_OK);
  const facts = lastJson(io)["readiness"] as Array<{id: string; status: string; hint: string}>;
  for (const id of ["model", "permissions", "software", "storage", "execution"]) {
    assert.equal(facts.find((fact) => fact.id === id)?.status, "ok");
  }
  assert.ok(!JSON.stringify(server.sent).includes("acp_research_model_plan_v1\",\"arguments"));
});

test("wait binds the platform-assembled evidence report and persists the record", async () => {
  const { file, env } = setup();
  // evidence:false — no status artifacts; the wait hook must obtain the report
  // reference from acp_research_evidence_prepare_v1 instead.
  const server = gatewayServer({ approved: true, evidence: false });
  const io = captureIo(env);
  const code = await main(["research", "--question", "energy", "--recipe-digest", digest("a"),
    "--science", file, "--research", "stable-product-seed", "--json"], { createTransport: () => server.transport }, io);
  assert.equal(code, EXIT_OK, JSON.stringify(lastJson(io)));
  const frames = server.sent.filter((frame) => frame["method"] === "tools/call").map((frame) => frame["params"] as Record<string, unknown>);
  const prepare = frames.find((params) => params["name"] === "acp_research_evidence_prepare_v1");
  assert.ok(prepare, "the wait hook must call the evidence prepare tool");
  const result = lastJson(io)["result"] as Record<string, unknown>;
  assert.notEqual(result["stop_reason"], "evidence_binding_required");
  assert.ok(frames.some((params) => params["name"] === "acp_research_record_create_v1"),
    "a bound report must let the decision record persist");
});

test("singlepoint mapper rejects unconfirmed resource/budget/input changes", () => {
  const input = { schema: "acp2.skill.planner.input.v1", skill: "planner", skill_version: PLANNER_SKILL_VERSION, project_id: "proj-1", round: 0,
    hypothesis: "energy", normalized_question: "energy", recipe_digest: digest("a"), plan_definition_digest: digest("b"),
    initial_input: science.initial_input, science_seed: science.science_seed, critique_findings: [], evidence: null, remaining_budget: budget } as const;
  const plan = runFixturePlanner(input);
  const supported = { ...plan, science: { ...plan.science, capability_steps: [{ step_index: 0,
    capability: "electronic.energy", version: "1.0.0", parameters: { ncores: 1, memory_gb: 1 } }] } };
  assert.equal(singlepointPayload(supported, science).workflow, "singlepoint");
  assert.throws(() => singlepointPayload(supported, { ...science, submission: { ...science.submission, resources: { ncores: 2, memory_gb: 1 } } }), /ncores/);
  assert.throws(() => singlepointPayload(supported, { ...science, submission: { ...science.submission, budget: {} } }), /budget/);
  assert.throws(() => singlepointPayload(supported, { ...science, submission: { ...science.submission, input: { ...science.submission.input, xyz_text: "changed" } } }), /input bytes/);
});


test("default fake trajectory binds evidence and persists the decision; CAS refusal never accepts", async () => {
  for (const writerFails of [false, true]) {
    const { file, env } = setup();
    const server = gatewayServer({ approved: true, evidence: true, writerFails });
    const io = captureIo(env);
    const code = await main(["research", "--question", "energy", "--recipe-digest", digest("a"),
      "--science", file, "--research", "stable-product-seed", "--json"], { createTransport: () => server.transport }, io);
    assert.equal(code, EXIT_OK, JSON.stringify(lastJson(io)));
    const result = lastJson(io)["result"] as Record<string, unknown>;
    assert.equal(result["stop_reason"], writerFails ? "persist_failed" : "accepted", JSON.stringify(lastJson(io)));
    assert.equal(result["decisions"], writerFails ? 0 : 1);
    assert.equal(result["bindings"], 1);
  }
});

test("model clarification through default product parts makes zero proposals or submits", async () => {
  const { file, env } = setup();
  const server = gatewayServer({ clarification: true });
  const io = captureIo(env);
  const code = await main(["research", "--question", "energy", "--recipe-digest", digest("a"),
    "--science", file, "--json"], { createTransport: () => server.transport }, io);
  assert.equal(code, 1);
  assert.match(JSON.stringify(lastJson(io)), /clarification/);
  const frames = server.sent.filter((frame) => frame["method"] === "tools/call").map((frame) => frame["params"] as Record<string, unknown>);
  assert.ok(!frames.some((params) => ["acp_submit", "acp_research_proposal_create_v1"].includes(String(params["name"]))));
});


test("installed resume restores approval then waiting checkpoint without another model call or submit", async () => {
  const { file, env } = setup();
  const options = { approved: false, pending: true, evidence: false };
  const server = gatewayServer(options);
  const research = captureIo(env);
  assert.equal(await main(["research", "--question", "energy", "--recipe-digest", digest("a"),
    "--science", file, "--research", "stable-product-seed", "--json"], { createTransport: () => server.transport }, research), EXIT_OK);
  assert.equal((lastJson(research)["result"] as Record<string, unknown>)["stop_reason"], "approval_rejected");
  options.approved = true;
  const args = ["resume", "--proposal", "prop-ses-product", "--plan-digest", digest("c"), "--json"];
  const waiting = captureIo(env);
  assert.equal(await main(args, { createTransport: () => server.transport }, waiting), EXIT_OK, JSON.stringify(lastJson(waiting)));
  assert.equal(lastJson(waiting)["status"], "waiting", JSON.stringify(lastJson(waiting)));
  const foreign = captureIo(env);
  assert.equal(await main([...args, "--task", "other-task"], { createTransport: () => server.transport }, foreign), 1);
  assert.match(JSON.stringify(lastJson(foreign)), /resume_binding_mismatch/);
  const jumped = captureIo(env);
  assert.equal(await main([...args, "--cursor", "unverified-cursor"], { createTransport: () => server.transport }, jumped), 1);
  assert.match(JSON.stringify(lastJson(jumped)), /resume_cursor_mismatch/);
  options.pending = false;
  options.evidence = true;
  const resumed = captureIo(env);
  assert.equal(await main(args, { createTransport: () => server.transport }, resumed), EXIT_OK, JSON.stringify(lastJson(resumed)));
  assert.equal((lastJson(resumed)["result"] as Record<string, unknown>)["stop_reason"], "accepted", JSON.stringify(lastJson(resumed)));
  const calls = server.sent.filter((frame) => frame["method"] === "tools/call").map((frame) => (frame["params"] as Record<string, unknown>)["name"]);
  assert.equal(calls.filter((tool) => tool === "acp_research_model_plan_v1").length, 1);
  assert.equal(calls.filter((tool) => tool === "acp_submit").length, 1);
  const changed = captureIo(env);
  assert.equal(await main(["resume", "--proposal", "prop-ses-product", "--plan-digest", digest("d"), "--json"],
    { createTransport: () => server.transport }, changed), 1);
  assert.match(JSON.stringify(lastJson(changed)), /resume_binding_mismatch/);
});


test("doctor redacts a credential even when a failed transport echoes it", async () => {
  const secret = "fake-secret-for-redaction-test";
  const report = await runDoctor({ environment: "dev", productVersion: CLI_VERSION,
    env: { RPH_CONTROLLER_API_KEY: secret }, connect: async () => { throw new Error(`fake transport error ${secret}`); } });
  assert.equal(report.ready, false);
  assert.equal(report.status, "failed");
  assert.ok(!JSON.stringify(report).includes(secret));
});


test("default product refuses failed Controller readiness before a model call or submit", async () => {
  const { file, env } = setup();
  const server = gatewayServer({ unready: true });
  const io = captureIo(env);
  assert.equal(await main(["research", "--question", "energy", "--recipe-digest", digest("a"),
    "--science", file, "--json"], { createTransport: () => server.transport }, io), 3);
  assert.match(JSON.stringify(lastJson(io)), /controller_not_ready/);
  const frames = server.sent.filter((frame) => frame["method"] === "tools/call").map((frame) => frame["params"] as Record<string, unknown>);
  assert.ok(!frames.some((params) => ["acp_submit", "acp_research_model_plan_v1"].includes(String(params["name"]))));
});
