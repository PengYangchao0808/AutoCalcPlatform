import assert from "node:assert/strict";
import { test } from "node:test";

import {
  BRIDGE_EXTENSION_ID,
  BRIDGE_EXTENSION_VERSION,
  BRIDGE_MODEL_PREFIX,
  BRIDGE_OPERATIONS,
  Bridge,
  BridgeContractError,
  OPERATION_FIELDS,
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
  missingFieldsFor,
  negotiatesResearchExtension,
  projectToolResult,
  resolveDaPhase,
  stripModelPrefix,
  toModelToolName,
  type BridgeField,
  type BridgeOperation,
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

function contractParams(error: unknown): Readonly<Record<string, unknown>> {
  return error instanceof BridgeContractError ? error.error.params : {};
}

function omitField(request: BridgeRequest, field: BridgeField): BridgeRequest {
  const copy: Record<string, unknown> = { ...request };
  delete copy[field];
  return copy as unknown as BridgeRequest;
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
    artifactDigest: "sha256:" + "e".repeat(64),
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
    generation: 2,
    idempotencyKey: "idem-prepare",
  });
  assert.equal(prepare.isError, false);
  const read = await bridge.execute({
    operation: "read",
    task: "job-1",
    attempt: 1,
    generation: 9,
    resource: "freq.out",
    artifactDigest: "sha256:" + "e".repeat(64),
  });
  assert.equal(read.isError, false);
  assert.deepEqual(
    client.calls.map((call) => call.name),
    ["acp_prepare_artifact", "acp_read_artifact"],
  );
  // M1-01 frozen read table: the complete artifact_ref, never the pre-M1-01
  // flat `job_id`/`resource_id` spelling.
  assert.deepEqual(client.calls[1]?.params, {
    artifact_ref: {
      task_id: "job-1",
      attempt: 1,
      artifact_id: "freq.out",
      generation: 9,
      digest: "sha256:" + "e".repeat(64),
    },
  });
  // D-IDENT-10 (M0-11): the prepare arguments are the v1 HTTP prepare body. The
  // artifact is `artifact_id` (`resource_id` is server-derived and would be an
  // unknown field), and `purpose` is the Controller-required field.
  assert.deepEqual(client.calls[0]?.params, {
    purpose: "artifact",
    job_id: "job-1",
    attempt: 1,
    artifact_id: "freq.out",
    generation: 2,
    idempotency_key: "idem-prepare",
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
  assert.equal(second, first);
  assert.equal(client.calls.length, 1);
  assert.deepEqual(client.calls[0]?.params, {
    workflow: "xtb_optimize",
    input: { smiles: "CCO" },
    method: { level: "gfn2" },
    project_id: "proj-1",
    idempotency_key: "idem-1",
  });
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
    { purpose: "artifact", job_id: "job-1", attempt: 3, artifact_id: "freq.out" },
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

const POSITIVE_REQUESTS: Record<BridgeOperation, BridgeRequest> = {
  discover: { operation: "discover" },
  validate: {
    operation: "validate",
    project: "proj-1",
    workflow: "xtb_optimize",
    input: { smiles: "CCO" },
    method: { level: "gfn2" },
    idempotencyKey: "idem-validate",
  },
  submit: SUBMIT,
  status: { operation: "status", task: "job-1", attempt: 0 },
  evidence: {
    operation: "evidence",
    analysisId: "vibration.analyze",
    inputRefs: [
      {
        task_id: "job-1",
        attempt: 1,
        artifact_id: "freq.out",
        generation: 1,
        digest: "sha256:" + "e".repeat(64),
      },
    ],
    explicit: true,
  },
  prepare: {
    operation: "prepare",
    task: "job-1",
    attempt: 0,
    resource: "freq.out",
  },
  read: {
    operation: "read",
    task: "job-1",
    attempt: 0,
    generation: 1,
    resource: "freq.out",
    artifactDigest: "sha256:" + "e".repeat(64),
  },
};

test("per-operation required-field matrix accepts positives and rejects every missing field", TEST_OPTIONS, async () => {
  for (const operation of BRIDGE_OPERATIONS) {
    const request = POSITIVE_REQUESTS[operation];
    assert.deepEqual(missingFieldsFor(request), [], `${operation}:positive`);
    const required = OPERATION_FIELDS[operation];
    for (const field of required) {
      const incomplete = omitField(request, field);
      assert.deepEqual(
        missingFieldsFor(incomplete),
        [field],
        `${operation}:missing:${field}`,
      );
      const client = recordingClient(() => okResult({}));
      await assert.rejects(
        () => new Bridge(client).execute(incomplete),
        (error: unknown) => {
          assert.equal(contractCode(error), "input_invalid", `${operation}:${field}`);
          assert.equal(contractRule(error), "missing_required_field");
          const fields = contractParams(error)["fields"];
          assert.equal(
            Array.isArray(fields) && fields.includes(field),
            true,
            `${operation}:${field}:fields=${JSON.stringify(fields)}`,
          );
          assert.equal(client.calls.length, 0);
          return true;
        },
      );
    }
  }
  assert.equal(OPERATION_FIELDS.discover.length, 0);
  assert.equal(OPERATION_FIELDS.validate.includes("generation"), false);
  assert.equal(OPERATION_FIELDS.submit.includes("generation"), false);
  assert.equal(OPERATION_FIELDS.status.includes("project"), false);
  assert.equal(OPERATION_FIELDS.status.includes("generation"), false);
  assert.deepEqual(
    missingFieldsFor({ operation: "discover", task: "job-1", generation: 2 }),
    [],
  );
  assert.deepEqual(
    missingFieldsFor({
      operation: "submit",
      project: "proj-1",
      input: { smiles: "CCO" },
      idempotencyKey: "idem-no-generation",
    }),
    [],
  );
});

test("model-visible prefix stripping is exactly once and raw prefixes are rejected", TEST_OPTIONS, () => {
  assert.equal(BRIDGE_MODEL_PREFIX, "mcp__acp__");
  assert.equal(
    stripModelPrefix(`${BRIDGE_MODEL_PREFIX}acp_submit`),
    "acp_submit",
  );
  assert.equal(stripModelPrefix(toModelToolName("acp_submit")), "acp_submit");
  assert.throws(() => stripModelPrefix("acp_submit"), /not model-visible/);
  assert.throws(
    () =>
      stripModelPrefix(
        `${BRIDGE_MODEL_PREFIX}${BRIDGE_MODEL_PREFIX}acp_submit`,
      ),
    /must not contain mcp__acp__/,
  );
  assert.throws(
    () => assertRawToolName(`x${BRIDGE_MODEL_PREFIX}acp_submit`),
    /must not contain mcp__acp__/,
  );
  assert.throws(
    () => toModelToolName(`${BRIDGE_MODEL_PREFIX}acp_submit`),
    /must not contain mcp__acp__/,
  );
});

test("read before prepare returns the exact not_prepared envelope without preparing", TEST_OPTIONS, async () => {
  const client = recordingClient(() => okResult({ content: "leaked" }));
  const request: BridgeRequest = {
    operation: "read",
    task: "job-9",
    attempt: 2,
    generation: 4,
    resource: "freq.out",
    artifactDigest: "sha256:" + "e".repeat(64),
  };
  const result = await new Bridge(client).execute(request);
  assert.equal(client.calls.length, 0);
  assert.deepEqual(Object.keys(result.structuredContent).sort(), [
    "data",
    "diagnostic_ref",
    "dry_run",
    "error",
    "exit_code",
    "operation",
    "operation_identity",
    "schema_version",
    "status",
  ]);
  assert.equal(result.structuredContent["schema_version"], 1);
  assert.equal(result.structuredContent["operation"], "acp_read_artifact");
  assert.equal(result.structuredContent["status"], "error");
  assert.equal(result.structuredContent["exit_code"], 2);
  assert.equal(result.structuredContent["dry_run"], false);
  assert.equal(result.structuredContent["operation_identity"], null);
  assert.equal(result.structuredContent["diagnostic_ref"], null);
  assert.equal(result.structuredContent["data"], null);
  const error = result.structuredContent["error"] as Record<string, unknown>;
  assert.deepEqual(Object.keys(error).sort(), [
    "code",
    "diagnostic_ref",
    "message",
    "params",
    "retryable",
  ]);
  assert.equal(error["code"], "not_prepared");
  assert.equal(error["retryable"], true);
  assert.equal(error["diagnostic_ref"], null);
  assert.deepEqual(error["params"], {
    rule: "prepare_required",
    job_id: "job-9",
    attempt: 2,
    resource_id: "freq.out",
  });
  assert.equal(result.projection.outcome, "error");
  assert.equal(result.projection.isError, true);
  assert.equal(result.projection.reference, null);
  assert.equal(result.projection.digest, null);
  assert.equal(result.projection.errorCode, "not_prepared");
});

test("oversized artifacts project only a digest and reference", TEST_OPTIONS, async () => {
  const payload = "Z".repeat(2_000_000);
  const digest = "c".repeat(64);
  const client = recordingClient(() =>
    okResult({
      reference: { kind: "operation", id: "op-big" },
      digest,
      content: payload,
    }),
  );
  const result = await new Bridge(client).execute({
    operation: "prepare",
    task: "job-big",
    attempt: 0,
    resource: "trajectory.xyz",
  });
  assert.equal(result.isError, false);
  const projectionJson = JSON.stringify(result.projection);
  assert.equal(projectionJson.includes("ZZZZZZZZ"), false);
  assert.equal(projectionJson.length < 512, true);
  assert.equal(result.projection.digest, digest);
  assert.deepEqual(result.projection.reference, {
    kind: "operation",
    id: "op-big",
  });
  assert.deepEqual(Object.keys(result.projection).sort(), [
    "digest",
    "errorCode",
    "isError",
    "outcome",
    "reference",
    "tool",
  ]);
  const data = result.structuredContent["data"] as Record<string, unknown>;
  assert.equal(typeof data["content"], "string");
  assert.equal((data["content"] as string).length, payload.length);
});

test("credential-bearing and absolute-path results are refused", TEST_OPTIONS, async () => {
  const credentialClient = recordingClient(() =>
    okResult({
      reference: { kind: "job", id: "job-1" },
      access_token: `sk-${"a".repeat(24)}`,
    }),
  );
  await assert.rejects(
    () =>
      new Bridge(credentialClient).execute({
        ...SUBMIT,
        idempotencyKey: "idem-credential",
      }),
    (error: unknown) => {
      assert.equal(contractCode(error), "access_denied");
      assert.equal(contractRule(error), "credential_in_result");
      const path = String(contractParams(error)["path"]);
      assert.equal(path.includes("access_token"), true);
      return true;
    },
  );
  assert.equal(credentialClient.calls.length, 1);

  const pathClient = recordingClient(() =>
    okResult({
      reference: { kind: "job", id: "job-1" },
      artifact_path: "/scratch/user42/job-1/freq.out",
    }),
  );
  await assert.rejects(
    () =>
      new Bridge(pathClient).execute({
        ...SUBMIT,
        idempotencyKey: "idem-path",
      }),
    (error: unknown) => {
      assert.equal(contractCode(error), "access_denied");
      assert.equal(contractRule(error), "absolute_path_in_result");
      return true;
    },
  );

  const uriClient = recordingClient(() =>
    okResult({ location: "sftp://node01/scratch/job-1/out" }),
  );
  await assert.rejects(
    () =>
      new Bridge(uriClient).execute({
        ...SUBMIT,
        idempotencyKey: "idem-uri",
      }),
    (error: unknown) => contractRule(error) === "absolute_path_in_result",
  );
});

test("isError and structuredContent pass through byte-for-byte", TEST_OPTIONS, async () => {
  const errorEnvelope: BridgeToolResult = deepFreeze({
    isError: true,
    structuredContent: {
      schema_version: 1,
      status: "error",
      exit_code: 1,
      data: null,
      error: {
        code: "conflict",
        message: "revision conflict",
        params: { revision: 3 },
        retryable: false,
      },
    },
  });
  const errorResult = await new Bridge(
    recordingClient(() => errorEnvelope),
  ).execute({ ...SUBMIT, idempotencyKey: "idem-passthrough-e" });
  assert.equal(errorResult.isError, true);
  assert.equal(errorResult.structuredContent, errorEnvelope.structuredContent);
  assert.deepEqual(errorResult.projection, {
    tool: "acp_submit",
    outcome: "error",
    isError: true,
    reference: null,
    digest: null,
    errorCode: "conflict",
  });

  const okEnvelope: BridgeToolResult = deepFreeze({
    isError: false,
    structuredContent: {
      schema_version: 1,
      status: "ok",
      exit_code: 0,
      data: { reference: { kind: "job", id: "job-2" }, note: "unchanged" },
      error: null,
    },
  });
  const ok = await new Bridge(recordingClient(() => okEnvelope)).execute({
    ...SUBMIT,
    idempotencyKey: "idem-passthrough-o",
  });
  assert.equal(ok.isError, false);
  assert.equal(ok.structuredContent, okEnvelope.structuredContent);
  assert.deepEqual(ok.projection.reference, { kind: "job", id: "job-2" });
});

test("a different idempotency key creates a new reference", TEST_OPTIONS, async () => {
  let sequence = 0;
  const client = recordingClient(() =>
    okResult({ reference: { kind: "job", id: `job-${++sequence}` } }),
  );
  const bridge = new Bridge(client);
  const first = await bridge.execute({ ...SUBMIT, idempotencyKey: "idem-a" });
  const second = await bridge.execute({ ...SUBMIT, idempotencyKey: "idem-b" });
  assert.equal(client.calls.length, 2);
  assert.equal((first.projection.reference as { id: string }).id, "job-1");
  assert.equal((second.projection.reference as { id: string }).id, "job-2");
});

test("cross-project and cross-attempt reads are refused", TEST_OPTIONS, async () => {
  const client = recordingClient(() =>
    okResult({ reference: { kind: "operation", id: "op-1" } }),
  );
  const bridge = new Bridge(client);
  await bridge.execute({
    operation: "prepare",
    project: "proj-a",
    task: "job-1",
    attempt: 0,
    resource: "freq.out",
  });
  await assert.rejects(
    () =>
      bridge.execute({
        operation: "read",
        project: "proj-b",
        task: "job-1",
        attempt: 0,
        generation: 3,
        resource: "freq.out",
        artifactDigest: "sha256:" + "e".repeat(64),
      }),
    (error: unknown) => {
      assert.equal(contractCode(error), "access_denied");
      assert.equal(contractRule(error), "project_ownership_mismatch");
      assert.equal(contractParams(error)["expected_project"], "proj-a");
      assert.equal(contractParams(error)["observed_project"], "proj-b");
      return true;
    },
  );
  await assert.rejects(
    () =>
      bridge.execute({
        operation: "read",
        project: "proj-a",
        task: "job-1",
        attempt: 1,
        generation: 3,
        resource: "freq.out",
        artifactDigest: "sha256:" + "e".repeat(64),
      }),
    (error: unknown) => {
      assert.equal(contractCode(error), "access_denied");
      assert.equal(contractRule(error), "attempt_ownership_mismatch");
      assert.equal(contractParams(error)["expected_attempt"], 0);
      assert.equal(contractParams(error)["observed_attempt"], 1);
      return true;
    },
  );
  const read = await bridge.execute({
    operation: "read",
    project: "proj-a",
    task: "job-1",
    attempt: 0,
    generation: 3,
    resource: "freq.out",
    artifactDigest: "sha256:" + "e".repeat(64),
  });
  assert.equal(read.isError, false);
  assert.deepEqual(
    client.calls.map((call) => call.name),
    ["acp_prepare_artifact", "acp_read_artifact"],
  );
});
