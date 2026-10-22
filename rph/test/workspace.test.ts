import assert from "node:assert/strict";
import { test } from "node:test";

import {
  isSameRecord,
  memoryKey,
  proposalKey,
  recordKey,
  withRecordRevision,
} from "@rph/research";
import {
  BRIDGE_MODEL_PREFIX,
  BRIDGE_OPERATIONS,
  OPERATION_FIELDS,
  assertRawToolName,
  missingFieldsFor,
  requiredFieldsFor,
  stripModelPrefix,
  type BridgeOperation,
} from "@rph/bridge";
import {
  BUNDLE_PROFILE_NAMES,
  DSH_MCP_CLIENT_PLUGIN,
  bundleProfile,
} from "@rph/dsh-bundle";
import { CliUsageError, parseArgs, usage } from "@rph/cli";
import { TEST_OPTIONS } from "./harness.js";

test("research keys are namespaced, revision-bound and pure", TEST_OPTIONS, () => {
  const ref = { projectId: "proj-1", recordId: "rec-9", revision: 3 };
  assert.equal(recordKey(ref), "proj-1:record:rec-9@3");
  assert.equal(isSameRecord(ref, { ...ref }), true);
  assert.equal(isSameRecord(ref, { ...ref, revision: 4 }), false);
  const moved = withRecordRevision(ref, 4);
  assert.equal(recordKey(moved), "proj-1:record:rec-9@4");
  assert.equal(ref.revision, 3, "input reference must not be mutated");
});

test("research memory keys keep record and proposal kinds distinct", () => {
  assert.equal(
    memoryKey({ projectId: "p", kind: "record", id: "x", revision: 1 }),
    "p:record:x@1",
  );
  assert.equal(
    memoryKey({ projectId: "p", kind: "proposal", id: "x", revision: 1 }),
    "p:proposal:x@1",
  );
  assert.equal(
    proposalKey({ projectId: "p", proposalId: "x", approvalRef: null }),
    "p:proposal:x@unapproved",
  );
});

test("bridge operation list is exhaustive and stable", () => {
  assert.deepEqual([...BRIDGE_OPERATIONS], [
    "discover",
    "validate",
    "submit",
    "status",
    "evidence",
    "prepare",
    "read",
  ]);
  assert.equal(BRIDGE_MODEL_PREFIX, "mcp__acp__");
});

test("per-operation required-field matrix is exact", () => {
  const expected: Record<BridgeOperation, readonly string[]> = {
    discover: [],
    validate: ["project", "input", "idempotencyKey"],
    submit: ["project", "input", "idempotencyKey"],
    status: ["task", "attempt"],
    evidence: ["task", "attempt", "resource"],
    prepare: ["task", "attempt", "resource"],
    read: ["task", "attempt", "generation", "resource"],
  };
  for (const operation of BRIDGE_OPERATIONS) {
    assert.deepEqual(requiredFieldsFor(operation), expected[operation]);
  }
});

test("discovery needs neither task nor generation", () => {
  const fields = requiredFieldsFor("discover");
  assert.equal(fields.includes("task"), false);
  assert.equal(fields.includes("generation"), false);
  assert.deepEqual(missingFieldsFor({ operation: "discover" }), []);
});

test("validate and submit require project/input/idempotency but never generation", () => {
  for (const operation of ["validate", "submit"] as const) {
    const fields = requiredFieldsFor(operation);
    assert.deepEqual([...fields], ["project", "input", "idempotencyKey"]);
    assert.equal(fields.includes("generation"), false);
    assert.deepEqual(missingFieldsFor({ operation }), [
      "project",
      "input",
      "idempotencyKey",
    ]);
    assert.deepEqual(
      missingFieldsFor({
        operation,
        project: "p",
        input: { method: "r2SCAN-3c" },
        idempotencyKey: "idem-1",
      }),
      [],
    );
  }
  assert.deepEqual(OPERATION_FIELDS.status, ["task", "attempt"]);
});

test("status/prepare/read matrix matches the frozen semantics", () => {
  assert.deepEqual(missingFieldsFor({ operation: "status", task: "t" }), [
    "attempt",
  ]);
  assert.deepEqual(
    missingFieldsFor({ operation: "prepare", task: "t", attempt: 0 }),
    ["resource"],
  );
  assert.deepEqual(
    missingFieldsFor({
      operation: "read",
      task: "t",
      attempt: 0,
      resource: "r",
    }),
    ["generation"],
  );
  assert.deepEqual(
    missingFieldsFor({
      operation: "read",
      task: "t",
      attempt: 0,
      resource: "r",
      generation: 0,
    }),
    [],
  );
});

test("raw tool names reject the model-visible prefix", () => {
  assert.equal(assertRawToolName("acp_status"), "acp_status");
  assert.throws(
    () => assertRawToolName("mcp__acp__acp_status"),
    /must not contain mcp__acp__/,
  );
  assert.equal(stripModelPrefix("mcp__acp__acp_submit"), "acp_submit");
  assert.throws(() => stripModelPrefix("acp_submit"), /not model-visible/);
});

test("dsh bundle names only public plugin interfaces", () => {
  assert.equal(DSH_MCP_CLIENT_PLUGIN, "@deepseek-ai/dsh-mcp-client");
  assert.deepEqual([...BUNDLE_PROFILE_NAMES], ["web", "headless"]);
  const web = bundleProfile("web");
  assert.equal(web.plugin, DSH_MCP_CLIENT_PLUGIN);
  assert.equal(web.localOnly, true);
  assert.deepEqual([...web.bridgeOperations], [...BRIDGE_OPERATIONS]);
  assert.throws(() => bundleProfile("acp" as "web"), /unknown DSH profile/);
});

test("dsh profile payload has no private DSH source paths", () => {
  const serialized = JSON.stringify(bundleProfile("headless"));
  assert.equal(serialized.includes("deepseek-harness/"), false);
  assert.equal(serialized.includes("deepseek-harness/packages"), false);
  assert.equal(serialized.includes("@deepseek-ai/dsh-mcp-client"), true);
});

test("cli parser is pure and side-effect free", () => {
  assert.deepEqual(parseArgs([]), { command: "help" });
  assert.deepEqual(parseArgs(["--help"]), { command: "help" });
  assert.deepEqual(parseArgs(["--version"]), { command: "version" });
  assert.deepEqual(parseArgs(["--profile", "headless"]), {
    command: "profile",
    profile: "headless",
  });
});

test("cli parser rejects unknown arguments and missing profile values", () => {
  assert.throws(() => parseArgs(["--bogus"]), CliUsageError);
  assert.throws(() => parseArgs(["--profile"]), /requires a value/);
  assert.throws(() => parseArgs(["--profile", "prod"]), /unknown profile/);
  const text = usage();
  assert.equal(text.includes("--profile <web|headless>"), true);
  assert.equal(text.includes("never starts a Controller"), true);
});
