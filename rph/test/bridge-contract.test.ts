import assert from "node:assert/strict";
import { test } from "node:test";

import {
  BRIDGE_EXTENSION_ID,
  BRIDGE_EXTENSION_VERSION,
  BRIDGE_MODEL_PREFIX,
  BRIDGE_OPERATIONS,
  Bridge,
  BridgeContractError,
  P1_TOOL_NAMES,
  P2_OPERATION_MAP,
  P2_OPERATIONS,
  artifactKey,
  assertBridgeProjectionConsistent,
  assertOperationAllowed,
  assertRawToolName,
  buildP2Arguments,
  buildToolArguments,
  callP2Operation,
  isP2Operation,
  mapOperationToTool,
  mapP2Operation,
  negotiatesResearchExtension,
  projectToolResult,
  resolveDaPhase,
  toModelToolName,
  type BridgeProjection,
  type BridgeRequest,
  type BridgeToolResult,
  type PlatformClient,
  type P2Operation,
} from "@rph/bridge";
import { TEST_OPTIONS } from "./harness.js";

interface RecordedCall {
  readonly name: string;
  readonly params: Readonly<Record<string, unknown>>;
}

interface RecordingClient extends PlatformClient {
  readonly calls: RecordedCall[];
}

type ResultHandler = (
  name: string,
  params: Readonly<Record<string, unknown>>,
) => BridgeToolResult;

function recordingClient(handler?: ResultHandler): RecordingClient {
  const calls: RecordedCall[] = [];
  return {
    calls,
    async callTool(name, params) {
      calls.push({ name, params });
      return handler === undefined ? okResult({}) : handler(name, params);
    },
  };
}

function okResult(data: Readonly<Record<string, unknown>>): BridgeToolResult {
  return {
    isError: false,
    structuredContent: {
      schema_version: 1,
      status: "ok",
      exit_code: 0,
      data,
      error: null,
    },
  };
}

function errorResult(code: string): BridgeToolResult {
  return {
    isError: true,
    structuredContent: {
      schema_version: 1,
      status: "error",
      exit_code: 1,
      data: null,
      error: { code, message: code, params: {}, retryable: false },
    },
  };
}

function deepFreeze<T>(value: T): T {
  if (value !== null && typeof value === "object") {
    for (const key of Object.keys(value as Record<string, unknown>)) {
      deepFreeze((value as Record<string, unknown>)[key]);
    }
    Object.freeze(value);
  }
  return value;
}

function contractCode(error: unknown): string {
  return error instanceof BridgeContractError ? error.error.code : "not_a_contract_error";
}

function contractRule(error: unknown): string {
  if (!(error instanceof BridgeContractError)) {
    return "not_a_contract_error";
  }
  const rule = error.error.params["rule"];
  return typeof rule === "string" ? rule : "";
}

const SUBMIT: BridgeRequest = {
  operation: "submit",
  project: "proj-1",
  workflow: "xtb_optimize",
  input: { smiles: "CCO" },
  method: { level: "gfn2" },
  idempotencyKey: "idem-1",
};

test("p1 operations map to exactly one official mcp tool", TEST_OPTIONS, () => {
  const expected: Record<string, string> = {
    discover: "acp_capabilities",
    validate: "acp_preflight",
    submit: "acp_submit",
    status: "acp_status",
    evidence: "acp_analyze",
    prepare: "acp_prepare_artifact",
    read: "acp_read_artifact",
  };
  for (const operation of BRIDGE_OPERATIONS) {
    const mapping = mapOperationToTool(operation);
    assert.equal(mapping.tool, expected[operation]);
    assert.equal(P1_TOOL_NAMES.includes(mapping.tool), true);
    assert.equal(mapping.phase, null);
  }
  assert.equal(mapOperationToTool("submit").longTask, true);
  assert.equal(mapOperationToTool("prepare").longTask, true);
  assert.equal(mapOperationToTool("evidence").longTask, true);
  assert.equal(mapOperationToTool("status").longTask, false);
  assert.equal(mapOperationToTool("discover").longTask, false);
});

test("da phase dispatch is explicit and never defaults", TEST_OPTIONS, () => {
  assert.equal(
    mapOperationToTool("validate", "da-preflight").tool,
    "acp_preflight",
  );
  assert.equal(mapOperationToTool("submit", "da-submit").tool, "acp_submit");
  assert.equal(resolveDaPhase("validate", "da-preflight"), "da-preflight");
  assert.equal(resolveDaPhase("submit", "da-submit"), "da-submit");

  assert.throws(
    () => resolveDaPhase("validate"),
    (error: unknown) => contractRule(error) === "phase_required",
  );
  assert.throws(
    () => resolveDaPhase("submit", "da-preflight"),
    (error: unknown) => contractRule(error) === "phase_mismatch",
  );
  assert.throws(
    () => resolveDaPhase("status", "da-submit"),
    (error: unknown) => contractRule(error) === "phase_not_applicable",
  );
  assert.throws(
    () => mapOperationToTool("status", "da-submit"),
    (error: unknown) => contractRule(error) === "phase_not_applicable",
  );
});

test("p2 operations all map to tools in the committed 18-tool allowlist", TEST_OPTIONS, () => {
  const p2Tools = new Set<string>();
  for (const operation of P2_OPERATIONS) {
    assert.equal(isP2Operation(operation), true);
    const mapping = mapP2Operation(operation);
    assert.equal(mapping.operation, operation);
    assert.equal(mapping.toolVersion, BRIDGE_EXTENSION_VERSION);
    assert.equal(mapping.tool.includes(BRIDGE_MODEL_PREFIX), false);
    assert.match(mapping.tool, /^acp_research_[a-z0-9_]+_v[0-9]+$/);
    assert.match(mapping.namespacedId, /^acp\.research\.[a-z0-9_]+$/);
    assert.equal(mapping.errorCodes.length > 0, true);
    assert.equal(["T-25", "T-27", "T-28", "T-32"].includes(mapping.wiringTask), true);
    p2Tools.add(mapping.tool);
  }
  const allowlist = new Set<string>([...P1_TOOL_NAMES, ...p2Tools]);
  assert.equal(P1_TOOL_NAMES.length, 8);
  assert.equal(p2Tools.size, 10);
  assert.equal(allowlist.size, 18);
  for (const operation of P2_OPERATIONS) {
    assert.equal(allowlist.has(P2_OPERATION_MAP[operation].tool), true);
  }
  assert.equal(new Set(P2_OPERATIONS).size, P2_OPERATIONS.length);
});

test("p2 operation is refused when acp.research.v1 is not negotiated", TEST_OPTIONS, () => {
  assert.equal(negotiatesResearchExtension([]), false);
  assert.equal(negotiatesResearchExtension([BRIDGE_EXTENSION_ID]), true);
  assertOperationAllowed("submit", []);
  assert.throws(
    () => assertOperationAllowed("proposal.create", []),
    (error: unknown) => {
      assert.equal(contractCode(error), "unsupported_capability");
      assert.equal(contractRule(error), "extension_not_negotiated");
      return true;
    },
  );
  for (const operation of P2_OPERATIONS) {
    assert.throws(() => assertOperationAllowed(operation, []), BridgeContractError);
    assertOperationAllowed(operation, [{ id: BRIDGE_EXTENSION_ID, version: "1.0.0" }]);
  }
});

test("p2 negotiation rejects a mismatched extension version", TEST_OPTIONS, () => {
  assert.throws(
    () =>
      assertOperationAllowed("research.resume", [
        { id: BRIDGE_EXTENSION_ID, version: "2.0.0" },
      ]),
    (error: unknown) => contractRule(error) === "extension_version_mismatch",
  );
});

test("tool isError is never promoted to success", TEST_OPTIONS, async () => {
  const client = recordingClient(() => errorResult("access_denied"));
  const bridge = new Bridge(client);
  const result = await bridge.execute(SUBMIT);
  assert.equal(result.isError, true);
  assert.equal(result.projection.outcome, "error");
  assert.equal(result.projection.errorCode, "access_denied");
  assert.equal(result.projection.reference, null);
  assert.equal(client.calls.length, 1);
});

test("a projection reporting ok for isError=true is rejected", TEST_OPTIONS, async () => {
  const client = recordingClient(() => errorResult("resource_not_ready"));
  const bridge = new Bridge(client);
  const result = await bridge.execute(SUBMIT);
  assert.equal(result.projection.isError, true);
  const mutant = { ...result.projection, outcome: "ok" as const };
  assert.throws(
    () => assertBridgeProjectionConsistent(mutant as BridgeProjection),
    (error: unknown) => contractRule(error) === "error_reported_as_success",
  );
  const success = projectToolResult("acp_status", okResult({}));
  const inverse = { ...success, outcome: "error" as const };
  assert.throws(
    () => assertBridgeProjectionConsistent(inverse as BridgeProjection),
    (error: unknown) => contractRule(error) === "success_reported_as_error",
  );
});

test("structuredContent passes through unchanged and the request is not mutated", TEST_OPTIONS, async () => {
  const envelope = okResult({ reference: { kind: "job", id: "job-7" } });
  const client = recordingClient(() => envelope);
  const bridge = new Bridge(client);
  const request = deepFreeze({ ...SUBMIT });
  const result = await bridge.execute(request);
  assert.equal(result.structuredContent, envelope.structuredContent);
  assert.equal(result.isError, false);
  assert.deepEqual(request, { ...SUBMIT });
  const params = client.calls[0]?.params ?? {};
  assert.equal(Object.hasOwn(params, "charge"), false);
  assert.equal(Object.hasOwn(params, "spin"), false);
  assert.equal(Object.hasOwn(params, "generation"), false);
});

test("long task returns only the platform reference", TEST_OPTIONS, async () => {
  const client = recordingClient(() =>
    okResult({ reference: { kind: "job", id: "job-42" }, status: "queued" }),
  );
  const bridge = new Bridge(client);
  const result = await bridge.execute(SUBMIT);
  assert.deepEqual(result.projection.reference, { kind: "job", id: "job-42" });
  assert.deepEqual(Object.keys(result.projection).sort(), [
    "digest",
    "errorCode",
    "isError",
    "outcome",
    "reference",
    "tool",
  ]);
  assert.equal(result.projection.digest, null);
  assert.equal(Object.hasOwn(result.projection, "progress"), false);
  assert.equal(Object.hasOwn(result.projection, "status"), false);
});

test("artifact result exposes reference and digest only", TEST_OPTIONS, async () => {
  const digest = "a".repeat(64);
  const client = recordingClient(() =>
    okResult({
      reference: { kind: "operation", id: "op-3" },
      digest,
      content: "x".repeat(1_000_000),
    }),
  );
  const bridge = new Bridge(client);
  const result = await bridge.execute({
    operation: "prepare",
    task: "job-1",
    attempt: 0,
    resource: "freq.out",
  });
  assert.equal(result.projection.digest, digest);
  assert.deepEqual(result.projection.reference, {
    kind: "operation",
    id: "op-3",
  });
  assert.equal(JSON.stringify(result.projection).includes("xxxx"), false);
});

test("read without prepare returns structured not_prepared and never calls the tool", TEST_OPTIONS, async () => {
  const client = recordingClient(() => okResult({ content: "should not be read" }));
  const bridge = new Bridge(client);
  const result = await bridge.execute({
    operation: "read",
    task: "job-1",
    attempt: 0,
    generation: 2,
    resource: "freq.out",
  });
  assert.equal(client.calls.length, 0);
  assert.equal(result.isError, true);
  assert.equal(result.projection.outcome, "error");
  assert.equal(result.projection.errorCode, "not_prepared");
  const error = result.structuredContent["error"] as Record<string, unknown>;
  assert.equal(error["code"], "not_prepared");
  const params = error["params"] as Record<string, unknown>;
  assert.equal(params["rule"], "prepare_required");
  assert.equal(params["job_id"], "job-1");
});

test("prepare registers the artifact and read forwards the artifact identity", TEST_OPTIONS, async () => {
  const client = recordingClient((name) =>
    name === "acp_prepare_artifact"
      ? okResult({ reference: { kind: "operation", id: "op-1" } })
      : okResult({ reference: { kind: "operation", id: "op-1" }, content: "ok" }),
  );
  const bridge = new Bridge(client);
  const prepare = await bridge.execute({
    operation: "prepare",
    task: "job-1",
    attempt: 1,
    resource: "freq.out",
    idempotencyKey: "idem-prepare",
  });
  assert.equal(prepare.isError, false);
  const read = await bridge.execute({
    operation: "read",
    task: "job-1",
    attempt: 1,
    generation: 9,
    resource: "freq.out",
  });
  assert.equal(read.isError, false);
  assert.deepEqual(
    client.calls.map((call) => call.name),
    ["acp_prepare_artifact", "acp_read_artifact"],
  );
  assert.deepEqual(client.calls[1]?.params, {
    job_id: "job-1",
    attempt: 1,
    resource_id: "freq.out",
  });
  assert.equal(artifactKey({ task: "job-1", attempt: 1, resource: "freq.out" }), artifactKey({ task: "job-1", attempt: 1, resource: "freq.out" }));
});

test("raw mcp__acp__ tool names are rejected", TEST_OPTIONS, async () => {
  assert.throws(
    () => assertRawToolName("mcp__acp__acp_submit"),
    /must not contain mcp__acp__/,
  );
  assert.equal(
    toModelToolName("acp_submit"),
    `${BRIDGE_MODEL_PREFIX}acp_submit`,
  );
  const client = recordingClient(() => okResult({}));
  const bridge = new Bridge(client);
  await bridge.execute(SUBMIT);
  assert.equal(client.calls[0]?.name, "acp_submit");
  assert.equal(client.calls[0]?.name.includes(BRIDGE_MODEL_PREFIX), false);
});

test("repeated idempotent submit yields the same reference", TEST_OPTIONS, async () => {
  const client = recordingClient(() =>
    okResult({ reference: { kind: "job", id: "job-9" }, idempotent_replay: true }),
  );
  const bridge = new Bridge(client);
  const request = deepFreeze({ ...SUBMIT });
  const first = await bridge.execute(request);
  const second = await bridge.execute(request);
  assert.deepEqual(first.projection.reference, second.projection.reference);
  assert.equal(client.calls.length, 2);
  assert.deepEqual(client.calls[0]?.params, client.calls[1]?.params);
});

test("submit with a mutated method under one idempotency key is a conflict", TEST_OPTIONS, async () => {
  const client = recordingClient(() =>
    okResult({ reference: { kind: "job", id: "job-1" } }),
  );
  const bridge = new Bridge(client);
  await bridge.execute(deepFreeze({ ...SUBMIT }));
  await assert.rejects(
    () =>
      bridge.execute(
        deepFreeze({ ...SUBMIT, method: { level: "gfn1" } }),
      ),
    (error: unknown) => {
      assert.equal(contractCode(error), "revision_conflict");
      assert.equal(contractRule(error), "idempotency_key_reuse");
      return true;
    },
  );
  assert.equal(client.calls.length, 1);
});

test("project and attempt ownership are forwarded and never inferred", TEST_OPTIONS, async () => {
  const client = recordingClient(() =>
    okResult({ reference: { kind: "job", id: "job-1" } }),
  );
  const bridge = new Bridge(client);
  await bridge.execute({ ...SUBMIT, idempotencyKey: "idem-ownership" });
  const submitParams = client.calls[0]?.params ?? {};
  assert.equal(submitParams["project_id"], "proj-1");
  assert.equal(submitParams["idempotency_key"], "idem-ownership");
  assert.equal(Object.hasOwn(submitParams, "tenant"), false);
  assert.equal(Object.hasOwn(submitParams, "attempt"), false);
  assert.deepEqual(
    Object.keys(buildToolArguments({ operation: "status", task: "job-1", attempt: 0 })),
    ["reference"],
  );
  assert.deepEqual(
    buildToolArguments({
      operation: "prepare",
      task: "job-1",
      attempt: 3,
      resource: "freq.out",
    }),
    { job_id: "job-1", attempt: 3, resource_id: "freq.out" },
  );
});

test("p2 payload identity is refused", TEST_OPTIONS, async () => {
  assert.throws(
    () =>
      buildP2Arguments("approval.approve", {
        approval_ref: "ap-1",
        decision: "approve",
        expected_revision: 1,
        approver: "model",
      }),
    (error: unknown) => {
      assert.equal(contractCode(error), "access_denied");
      assert.equal(contractRule(error), "approver_identity_from_payload");
      return true;
    },
  );
  const client = recordingClient(() => okResult({}));
  await assert.rejects(
    () =>
      callP2Operation(
        client,
        "approval.approve",
        { approval_ref: "ap-1", decision: "approve", role: "admin" },
        [BRIDGE_EXTENSION_ID],
      ),
    BridgeContractError,
  );
  assert.equal(client.calls.length, 0);
});

test("approval decision must match the mapped operation", TEST_OPTIONS, () => {
  assert.throws(
    () => buildP2Arguments("approval.approve", { approval_ref: "ap-1" }),
    (error: unknown) => contractRule(error) === "decision_required",
  );
  assert.throws(
    () =>
      buildP2Arguments("approval.approve", {
        approval_ref: "ap-1",
        decision: "reject",
      }),
    (error: unknown) => contractCode(error) === "decision_mismatch",
  );
  assert.deepEqual(
    buildP2Arguments("approval.reject", {
      approval_ref: "ap-1",
      decision: "reject",
      expected_revision: 2,
    }),
    { approval_ref: "ap-1", decision: "reject", expected_revision: 2 },
  );
  assert.throws(
    () => buildP2Arguments("approval.query", { decision: "approve" }),
    (error: unknown) => contractRule(error) === "unexpected_decision",
  );
});

test("wait bounds are enforced", TEST_OPTIONS, () => {
  assert.throws(
    () => buildP2Arguments("research.wait", { kinds: ["approval"], max_wait_s: 600 }),
    (error: unknown) => contractRule(error) === "max_wait_s",
  );
  assert.throws(
    () => buildP2Arguments("research.wait", { kinds: ["approval"], limit: 201 }),
    (error: unknown) => contractRule(error) === "limit",
  );
  assert.deepEqual(
    buildP2Arguments("research.wait", {
      kinds: ["approval"],
      max_wait_s: 60,
      limit: 200,
      cursor: "c-1",
    }),
    { kinds: ["approval"], max_wait_s: 60, limit: 200, cursor: "c-1" },
  );
});

test("p2 execution keeps isError and projects the reference", TEST_OPTIONS, async () => {
  const errorClient = recordingClient(() => errorResult("access_denied"));
  const failed = await callP2Operation(
    errorClient,
    "approval.query",
    { approval_ref: "ap-1" },
    [{ id: BRIDGE_EXTENSION_ID, version: BRIDGE_EXTENSION_VERSION }],
  );
  assert.equal(failed.isError, true);
  assert.equal(failed.projection.outcome, "error");
  assert.equal(failed.projection.errorCode, "access_denied");
  assert.equal(failed.tool, "acp_research_approval_query_v1");

  const okClient = recordingClient(() =>
    okResult({ approval_ref: "ap-1", status: "approved", revision: 2 }),
  );
  const approved = await callP2Operation(
    okClient,
    "approval.approve",
    { approval_ref: "ap-1", decision: "approve", expected_revision: 1 },
    [BRIDGE_EXTENSION_ID],
  );
  assert.equal(approved.isError, false);
  assert.equal(approved.projection.reference, null);
  assert.deepEqual(okClient.calls[0]?.params, {
    approval_ref: "ap-1",
    decision: "approve",
    expected_revision: 1,
  });
});

test("projection never invents a reference", TEST_OPTIONS, () => {
  const projection = projectToolResult("acp_status", okResult({ status: "running" }));
  assert.equal(projection.reference, null);
  assert.equal(projection.digest, null);
  assert.equal(projection.outcome, "ok");
  const empty = projectToolResult("acp_capabilities", {
    isError: false,
    structuredContent: { data: { reference: "op-9" } },
  });
  assert.equal(empty.reference, "op-9");
  assert.equal(P2_OPERATIONS.includes("research.events" as P2Operation), true);
});
