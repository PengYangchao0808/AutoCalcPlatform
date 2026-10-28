/**
 * M3-01b — the M1-01 frozen argument tables, the single TaskRef normalizer and
 * the G21 retry invariants (Hermetic: injected `PlatformClient`, no process).
 *
 * Proven here:
 * - `prepare` forwards the frozen prepare body (`purpose`/`artifact_id`/
 *   `generation`/`expected_digest`) and never the server-derived `resource_id`;
 * - `read` sends the complete frozen `artifact_ref` and refuses a read without
 *   the digest instead of downgrading to the pre-M1-01 flat spelling;
 * - `evidence` is the explicit analysis direction (`analysis_id`/`input_refs`/
 *   `explicit`) and never the submit fields; an "evidence read" is refused with
 *   a typed rule that points at `read`;
 * - a submit keeps the **full** ADR-003 TaskRef through an idempotent replay, an
 *   incomplete reference is never defaulted (`attempt` is never 1, `release` is
 *   never `pending`) and is reconciled through `status`;
 * - one caller key used by two operations never replays the first result, two
 *   concurrent identical submits reach the platform once, two Bridge instances
 *   with the same research identity derive the same key, and a wrong
 *   project/attempt read is refused.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  Bridge,
  applyArtifactStatus,
  attributedSubmitBinding,
  buildToolArguments,
  preparedArtifactView,
  readRequestFor,
  reconcileTaskRef,
  submitTaskRef,
  submitWireKey,
  type BridgeRequest,
  type BridgeToolResult,
} from "@rph/bridge";

import { TEST_OPTIONS } from "./harness.js";

const DIGEST = "sha256:" + "e".repeat(64);
const PLAN = "sha256:" + "a".repeat(64);
const PROPOSAL = "sha256:" + "b".repeat(64);

interface RecordedCall {
  readonly name: string;
  readonly params: Readonly<Record<string, unknown>>;
}

function ok(data: Readonly<Record<string, unknown>>): BridgeToolResult {
  return { isError: false, structuredContent: { schema_version: 1, status: "ok", data } };
}

function fail(
  code: string,
  params: Readonly<Record<string, unknown>> = {},
): BridgeToolResult {
  return {
    isError: true,
    structuredContent: {
      schema_version: 1,
      status: "error",
      data: null,
      error: { code, message: code, params, retryable: false, diagnostic_ref: null },
    },
  };
}

function recording(handler: (call: RecordedCall) => BridgeToolResult | Promise<BridgeToolResult>) {
  const calls: RecordedCall[] = [];
  return {
    calls,
    client: {
      async callTool(name: string, params: Readonly<Record<string, unknown>>) {
        const call = { name, params };
        calls.push(call);
        return handler(call);
      },
    },
  };
}

function taskRefPayload(taskId: string, attempt = 2): Readonly<Record<string, unknown>> {
  return {
    task_id: taskId,
    job_id: taskId,
    project_id: "proj-1",
    attempt,
    release: { release_id: "rel-1", release_digest: "sha256:" + "c".repeat(64) },
  };
}

function submitRequest(overrides: Partial<BridgeRequest> = {}): BridgeRequest {
  return {
    operation: "submit",
    project: "proj-1",
    workflow: "fake",
    input: { source: "CCO", source_type: "smiles" },
    method: {},
    resources: { cores: 1 },
    planDigest: PLAN,
    proposalRef: "prop-1",
    proposalDigest: PROPOSAL,
    budget: { unit: "task_count", count: 1, timeout_s: 120 },
    research: "res-1",
    round: 0,
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// M1-01 frozen argument tables
// ---------------------------------------------------------------------------

test("prepare forwards generation/expected_digest and never resource_id", TEST_OPTIONS, () => {
  const args = buildToolArguments({
    operation: "prepare",
    task: "job-1",
    attempt: 2,
    resource: "input_preview",
    generation: 3,
    relativePath: "RESULT/input_preview.xyz",
    expectedDigest: DIGEST,
    idempotencyKey: "idem-prepare-1",
  });
  assert.deepEqual(args, {
    purpose: "artifact",
    job_id: "job-1",
    attempt: 2,
    artifact_id: "input_preview",
    generation: 3,
    relative_path: "RESULT/input_preview.xyz",
    expected_digest: DIGEST,
    idempotency_key: "idem-prepare-1",
  });
  assert.equal(Object.hasOwn(args, "resource_id"), false);
});

test("read sends the complete artifact_ref and refuses a missing digest", TEST_OPTIONS, () => {
  const args = buildToolArguments({
    operation: "read",
    task: "job-1",
    attempt: 2,
    resource: "input_preview",
    generation: 3,
    artifactDigest: DIGEST,
    maxBytes: 4096,
    encoding: "identity",
  });
  assert.deepEqual(args, {
    artifact_ref: {
      task_id: "job-1",
      attempt: 2,
      artifact_id: "input_preview",
      generation: 3,
      digest: DIGEST,
    },
    max_bytes: 4096,
    encoding: "identity",
  });
  // The pre-M1-01 flat spelling is dead: the frozen schema rejects it.
  for (const legacy of ["job_id", "resource_id", "digest"]) {
    assert.equal(Object.hasOwn(args, legacy), false, legacy);
  }

  assert.throws(
    () =>
      buildToolArguments({
        operation: "read",
        task: "job-1",
        attempt: 2,
        resource: "input_preview",
        generation: 3,
        artifactDigest: "not-a-digest",
      }),
    /artifact_ref_incomplete/,
  );
});

test("evidence is the analysis direction, never the submit field set", TEST_OPTIONS, async () => {
  const platform = recording(() => ok({ analysis_id: "vibration.analyze", started: false }));
  const bridge = new Bridge(platform.client);
  const inputRef = {
    task_id: "job-1",
    attempt: 2,
    artifact_id: "freq.out",
    generation: 1,
    digest: DIGEST,
  };
  const result = await bridge.execute({
    operation: "evidence",
    analysisId: "vibration.analyze",
    inputRefs: [inputRef],
    explicit: true,
    parameters: { window: 3 },
    idempotencyKey: "idem-analyze-1",
  });
  assert.equal(result.isError, false);
  assert.equal(result.tool, "acp_analyze");
  const sent = platform.calls[0]!.params;
  assert.deepEqual(sent, {
    analysis_id: "vibration.analyze",
    input_refs: [inputRef],
    explicit: true,
    parameters: { window: 3 },
    idempotency_key: "idem-analyze-1",
  });
  for (const forbidden of ["workflow", "input", "method", "resources", "project_id"]) {
    assert.equal(Object.hasOwn(sent, forbidden), false, forbidden);
  }
});

test("an evidence read is refused and pointed at the read operation", TEST_OPTIONS, async () => {
  const platform = recording(() => ok({}));
  const bridge = new Bridge(platform.client);
  await assert.rejects(
    () =>
      bridge.execute({
        operation: "evidence",
        task: "job-1",
        attempt: 1,
        resource: "report.json",
      }),
    (error: unknown) => {
      const contract = error as { error?: { code?: string; params?: { rule?: string } } };
      assert.equal(contract.error?.code, "evidence_read_is_not_analysis");
      assert.equal(contract.error?.params?.["rule"], "use_read_operation");
      return true;
    },
  );
  assert.equal(platform.calls.length, 0, "a refused read never starts computation");
});

test("an implicit analysis is refused before any call", TEST_OPTIONS, async () => {
  const platform = recording(() => ok({}));
  const bridge = new Bridge(platform.client);
  await assert.rejects(
    () =>
      bridge.execute({
        operation: "evidence",
        analysisId: "vibration.analyze",
        inputRefs: [{ task_id: "job-1" }],
      }),
    (error: unknown) => {
      const contract = error as { error?: { params?: { rule?: string } } };
      assert.equal(contract.error?.params?.["rule"], "explicit_required");
      return true;
    },
  );
  assert.equal(platform.calls.length, 0);
});

// ---------------------------------------------------------------------------
// TaskRef: one normalizer, no defaulted attempt/release
// ---------------------------------------------------------------------------

test("a submit keeps the full TaskRef, including a replay", TEST_OPTIONS, async () => {
  let calls = 0;
  const platform = recording(() => {
    calls += 1;
    return ok({ task_ref: taskRefPayload("job-9"), state: "queued" });
  });
  const bridge = new Bridge(platform.client);
  const first = await bridge.execute(submitRequest());
  assert.equal(first.taskRef?.task_id, "job-9");
  assert.equal(first.taskRef?.attempt, 2, "attempt is the platform's, never a default");
  assert.equal(first.taskRef?.release.release_id, "rel-1");
  assert.equal(first.taskRefReconciliation, "not_required");

  const replay = await bridge.execute(submitRequest());
  assert.equal(calls, 1, "the replay never calls the platform twice");
  assert.deepEqual(replay.taskRef, first.taskRef);
});

test("an incomplete reference is never defaulted to attempt 1 / release pending", TEST_OPTIONS, () => {
  const incomplete = submitTaskRef(
    ok({ task_ref: { task_id: "job-9", job_id: "job-9", project_id: "proj-1" } }),
    "proj-1",
  );
  assert.equal(incomplete.taskRef, null);
  assert.equal(incomplete.reconciliation, "required");
  assert.ok(incomplete.missing.includes("attempt"));
  assert.ok(incomplete.missing.some((field) => field.startsWith("release")));

  assert.throws(
    () =>
      attributedSubmitBinding(
        ok({ task_ref: { task_id: "job-9", project_id: "proj-1" } }),
        { project_id: "proj-1", research_id: "res-1", round: 0 },
      ),
    /task_ref_incomplete/,
  );
});

test("status reconciliation completes a reference, never guesses it", TEST_OPTIONS, () => {
  const completed = reconcileTaskRef(ok({ task_ref: taskRefPayload("job-9") }), {
    projectId: "proj-1",
    taskId: "job-9",
    attempt: 2,
  });
  assert.equal(completed.taskRef?.task_id, "job-9");
  assert.equal(completed.taskRef?.attempt, 2);
  assert.equal(completed.reconciliation, "not_required");

  const stillIncomplete = reconcileTaskRef(
    ok({ task_ref: { task_id: "job-9", project_id: "proj-1" } }),
    { projectId: "proj-1", taskId: "job-9" },
  );
  assert.equal(stillIncomplete.taskRef, null);
  assert.equal(stillIncomplete.reconciliation, "failed");
  assert.ok(stillIncomplete.missing.includes("attempt"));
});

// ---------------------------------------------------------------------------
// G21: cross-operation, concurrency, cross-instance, wrong target
// ---------------------------------------------------------------------------

test("one key used by two operations never replays the other operation", TEST_OPTIONS, async () => {
  const platform = recording((call) =>
    call.name === "acp_preflight"
      ? ok({ estimate: { cores: 1 } })
      : ok({ task_ref: taskRefPayload("job-1"), idempotent_replay: false }),
  );
  const bridge = new Bridge(platform.client);
  const key = "m301.cross-op.0001";
  const preflight = await bridge.execute({
    operation: "validate",
    project: "proj-1",
    workflow: "fake",
    input: { source: "CCO" },
    method: {},
    idempotencyKey: key,
  });
  const submit = await bridge.execute(submitRequest({ idempotencyKey: key }));
  assert.equal(preflight.tool, "acp_preflight");
  assert.equal(submit.tool, "acp_submit");
  assert.equal(platform.calls.length, 2, "both operations reached the platform");
  assert.equal(submit.taskRef?.task_id, "job-1");
});

test("concurrent identical submits reach the platform once", TEST_OPTIONS, async () => {
  let calls = 0;
  let release: () => void = () => {};
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const platform = recording(async () => {
    calls += 1;
    await gate;
    return ok({ task_ref: taskRefPayload("job-7") });
  });
  const bridge = new Bridge(platform.client);
  const pending = [bridge.execute(submitRequest()), bridge.execute(submitRequest())];
  // Let both callers reach the transport, then let the single call complete.
  await new Promise((resolve) => setImmediate(resolve));
  release();
  const results = await Promise.all(pending);
  const first = results[0]!;
  const second = results[1]!;
  assert.equal(calls, 1, "one in-flight platform call for one intent");
  assert.deepEqual(first.taskRef, second.taskRef);
  assert.equal(first.taskRef?.task_id, "job-7");
});

test("two Bridge instances derive the same key and replay the same task", TEST_OPTIONS, async () => {
  const keys = new Set<string>();
  const platform = recording((call) => {
    keys.add(String(call.params["idempotency_key"] ?? ""));
    return ok({ task_ref: taskRefPayload("job-2"), idempotent_replay: keys.size > 1 });
  });
  const first = await new Bridge(platform.client).execute(submitRequest());
  const second = await new Bridge(platform.client).execute(submitRequest());
  assert.equal(keys.size, 1, "the intent key is derived from the research identity");
  const [key] = [...keys];
  assert.equal(key, submitWireKey(key ?? ""));
  assert.deepEqual(second.taskRef, first.taskRef);
});

test("wrong project or attempt reads are refused", TEST_OPTIONS, async () => {
  const platform = recording(() => ok({ availability: "ready", artifact_ref: { resource_id: "a" } }));
  const bridge = new Bridge(platform.client);
  await bridge.execute({
    operation: "prepare",
    project: "proj-a",
    task: "job-1",
    attempt: 1,
    resource: "a",
  });
  await assert.rejects(
    () =>
      bridge.execute({
        operation: "read",
        project: "proj-b",
        task: "job-1",
        attempt: 1,
        generation: 1,
        resource: "a",
        artifactDigest: DIGEST,
      }),
    (error: unknown) => {
      const contract = error as { error?: { code?: string; params?: { rule?: string } } };
      assert.equal(contract.error?.code, "access_denied");
      assert.equal(contract.error?.params?.["rule"], "project_ownership_mismatch");
      return true;
    },
  );
  await assert.rejects(
    () =>
      bridge.execute({
        operation: "read",
        project: "proj-a",
        task: "job-1",
        attempt: 2,
        generation: 1,
        resource: "a",
        artifactDigest: DIGEST,
      }),
    (error: unknown) => {
      const contract = error as { error?: { code?: string; params?: { rule?: string } } };
      assert.equal(contract.error?.code, "access_denied");
      assert.equal(contract.error?.params?.["rule"], "attempt_ownership_mismatch");
      return true;
    },
  );
});

// ---------------------------------------------------------------------------
// F3/F4: prepare response → frozen artifact_ref, and the availability mapping
// ---------------------------------------------------------------------------

test("prepare response becomes a pending view, then a readable read request", TEST_OPTIONS, () => {
  const prepared = preparedArtifactView(
    ok({
      purpose: "artifact",
      availability: "absent",
      operation_ref: { kind: "operation", id: "op-1" },
      artifact_ref: { job_id: "job-1", resource_id: "input_preview", attempt: 1, digest: "" },
    }),
    { task: "job-1", attempt: 1, resource: "input_preview" },
  );
  assert.equal(prepared?.artifact_id, "input_preview", "resource_id is renamed to artifact_id");
  assert.equal(prepared?.digest, null);
  assert.equal(prepared?.availability, "unprepared");
  assert.equal(prepared?.operation_reference?.id, "op-1");
  assert.throws(
    () => readRequestFor(prepared!),
    (error: unknown) => {
      const contract = error as { error?: { code?: string; params?: { rule?: string } } };
      assert.equal(contract.error?.code, "artifact_ref_incomplete");
      assert.equal(contract.error?.params?.["rule"], "artifact_digest_required");
      return true;
    },
  );

  const ready = applyArtifactStatus(prepared!, ok({ availability: "ready", sha256: "f".repeat(64), generation: 1 }));
  assert.equal(ready.availability, "readable", "platform ready is the experience readable");
  assert.equal(ready.digest, "sha256:" + "f".repeat(64));
  const request = readRequestFor(ready, { project: "proj-1" });
  assert.deepEqual(request, {
    operation: "read",
    project: "proj-1",
    task: "job-1",
    attempt: 1,
    resource: "input_preview",
    generation: 1,
    artifactDigest: "sha256:" + "f".repeat(64),
  });
});
