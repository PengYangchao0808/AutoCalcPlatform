import assert from "node:assert/strict";
import { test } from "node:test";

import {
  Bridge,
  BridgeContractError,
  buildToolArguments,
  canDeriveSubmitKey,
  resolveSubmitIntentIdentity,
  type BridgeRequest,
  type BridgeToolResult,
  type PlatformClient,
} from "@rph/bridge";
import {
  MemorySubmitIntentStore,
  SubmitIntentConflict,
  SubmitIntentLedger,
  researchRoundKey,
  submitIntentKey,
  submitIntentScope,
  type SubmitIntent,
  type SubmitIntentStore,
} from "@rph/research";

import { TEST_OPTIONS } from "./harness.js";

const DIGEST = `sha256:${"a".repeat(64)}`;
const OTHER_DIGEST = `sha256:${"b".repeat(64)}`;
const PROJECT = "proj-diels-alder";
const RESEARCH = "res-diels-alder";

interface RecordedCall {
  readonly name: string;
  readonly params: Readonly<Record<string, unknown>>;
}

interface RecordingClient extends PlatformClient {
  readonly calls: RecordedCall[];
}

function okResult(): BridgeToolResult {
  return {
    isError: false,
    structuredContent: {
      schema_version: 1,
      status: "ok",
      exit_code: 0,
      data: {},
      error: null,
    },
  };
}

/** A client that records calls and the ledger/store order around them. */
function recordingClient(order: string[]): RecordingClient {
  const calls: RecordedCall[] = [];
  return {
    calls,
    async callTool(name, params) {
      order.push("call");
      calls.push({ name, params });
      return okResult();
    },
  };
}

class RecordingStore implements SubmitIntentStore {
  readonly order: string[];
  readonly rows = new Map<string, SubmitIntent>();

  constructor(order: string[]) {
    this.order = order;
  }

  load(scope: string): SubmitIntent | null {
    return this.rows.get(scope) ?? null;
  }

  save(intent: SubmitIntent): void {
    this.order.push("persist");
    this.rows.set(submitIntentScope(intent), intent);
  }
}

function submitRequest(overrides: Partial<BridgeRequest> = {}): BridgeRequest {
  return {
    operation: "submit",
    project: PROJECT,
    input: { xyz_text: "1\nH\nH 0 0 0.74\n" },
    workflow: "singlepoint",
    research: RESEARCH,
    round: 0,
    planDigest: DIGEST,
    ...overrides,
  };
}

test(
  "submit-intent: key binds project/research/round/plan digest and appends to the round key",
  TEST_OPTIONS,
  () => {
    const key = submitIntentKey({
      project_id: PROJECT,
      research_id: RESEARCH,
      round: 2,
      plan_definition_digest: DIGEST,
    });
    assert.equal(
      key,
      `${PROJECT}:record:${RESEARCH}:round-2:submit:plan-${"a".repeat(64)}`,
    );
    // The frozen round key is a strict prefix: the new segment is additive.
    assert.equal(
      researchRoundKey({ project_id: PROJECT, research_id: RESEARCH, round: 2 }),
      `${PROJECT}:record:${RESEARCH}:round-2`,
    );
    assert.ok(key.startsWith(`${PROJECT}:record:${RESEARCH}:round-2`));
    assert.equal(key.includes("sha256:"), false);
  },
);

test(
  "submit-intent: every key segment changes the derived key",
  TEST_OPTIONS,
  () => {
    const base = {
      project_id: PROJECT,
      research_id: RESEARCH,
      round: 0,
      plan_definition_digest: DIGEST,
    };
    const baseline = submitIntentKey(base);
    assert.notEqual(submitIntentKey({ ...base, project_id: "proj-other" }), baseline);
    assert.notEqual(submitIntentKey({ ...base, research_id: "res-other" }), baseline);
    assert.notEqual(submitIntentKey({ ...base, round: 1 }), baseline);
    assert.notEqual(
      submitIntentKey({ ...base, plan_definition_digest: OTHER_DIGEST }),
      baseline,
    );
    assert.equal(submitIntentKey(base), baseline);
    assert.throws(
      () => submitIntentKey({ ...base, plan_definition_digest: "sha256:ABC" }),
      /plan_definition_digest/,
    );
  },
);

test(
  "submit-intent: the ledger persists the key before the platform call",
  TEST_OPTIONS,
  async () => {
    const order: string[] = [];
    const store = new RecordingStore(order);
    const client = recordingClient(order);
    const bridge = new Bridge(client, { submitIntentStore: store });

    const request = submitRequest();
    const result = await bridge.execute(request);
    assert.equal(result.isError, false);

    // The key is persisted *before* the call; the post-call state update is
    // a second durable fact on the same key.
    assert.deepEqual(order, ["persist", "call", "persist"]);
    assert.ok(order.indexOf("persist") < order.indexOf("call"));
    assert.equal(client.calls.length, 1);
    assert.equal(
      client.calls[0]?.params["idempotency_key"],
      submitIntentKey({
        project_id: PROJECT,
        research_id: RESEARCH,
        round: 0,
        plan_definition_digest: DIGEST,
      }),
    );
    const persisted = bridge.submitIntents.load({
      project_id: PROJECT,
      research_id: RESEARCH,
      round: 0,
    });
    assert.notEqual(persisted, null);
    assert.equal(persisted?.state, "submitted");
  },
);

test(
  "submit-intent: a replay reuses the persisted key and does not call the platform twice",
  TEST_OPTIONS,
  async () => {
    const order: string[] = [];
    const store = new RecordingStore(order);
    const client = recordingClient(order);
    const bridge = new Bridge(client, { submitIntentStore: store });

    await bridge.execute(submitRequest());
    await bridge.execute(submitRequest());

    assert.equal(client.calls.length, 1);
    assert.equal(client.calls[0]?.params["idempotency_key"], store.rows.values().next().value?.key);
    // One persist for the derived key, one post-call state update, and the
    // second execute is a bridge replay (no new persist, no new call).
    assert.deepEqual(order, ["persist", "call", "persist"]);
  },
);

test(
  "submit-intent: a changed plan digest for the same round is a typed conflict",
  TEST_OPTIONS,
  async () => {
    const order: string[] = [];
    const store = new RecordingStore(order);
    const client = recordingClient(order);
    const bridge = new Bridge(client, { submitIntentStore: store });
    await bridge.execute(submitRequest());

    await assert.rejects(
      () => bridge.execute(submitRequest({ planDigest: OTHER_DIGEST })),
      (error: unknown) => {
        assert.ok(error instanceof BridgeContractError);
        assert.equal(error.error.class, "conflict");
        assert.equal(error.error.params["rule"], "plan_digest_changed");
        assert.equal(error.error.params["next_round"], 1);
        return true;
      },
    );
    // No second key was persisted and the old key was not rewritten.
    assert.equal(store.rows.size, 1);
    assert.equal(client.calls.length, 1);
    assert.equal([...store.rows.values()][0]?.plan_definition_digest, DIGEST);
  },
);

test(
  "submit-intent: a new round gets a new key after a plan change",
  TEST_OPTIONS,
  async () => {
    const ledger = new SubmitIntentLedger(new MemorySubmitIntentStore());
    const first = ledger.prepare({
      project_id: PROJECT,
      research_id: RESEARCH,
      round: 0,
      plan_definition_digest: DIGEST,
    });
    assert.equal(first.created, true);
    assert.throws(
      () =>
        ledger.prepare({
          project_id: PROJECT,
          research_id: RESEARCH,
          round: 0,
          plan_definition_digest: OTHER_DIGEST,
        }),
      (error: unknown) => {
        assert.ok(error instanceof SubmitIntentConflict);
        assert.equal(error.next_round, 1);
        assert.equal(error.previous_key, first.intent.key);
        return true;
      },
    );
    const second = ledger.prepare({
      project_id: PROJECT,
      research_id: RESEARCH,
      round: 1,
      plan_definition_digest: OTHER_DIGEST,
    });
    assert.equal(second.created, true);
    assert.notEqual(second.intent.key, first.intent.key);
    assert.equal(second.intent.round, 1);
  },
);

test(
  "submit-intent: a caller-supplied opaque key is forwarded verbatim and never rewritten",
  TEST_OPTIONS,
  async () => {
    const order: string[] = [];
    const client = recordingClient(order);
    const bridge = new Bridge(client, { submitIntentStore: new RecordingStore(order) });
    const opaque = "b7d1f4e2-0f0a-4c1e-9b7a-2f1c3d4e5f60";
    await bridge.execute(submitRequest({ idempotencyKey: opaque }));
    assert.equal(client.calls[0]?.params["idempotency_key"], opaque);
    // The platform-facing argument is byte-identical to the caller's key.
    assert.equal(
      buildToolArguments(submitRequest({ idempotencyKey: opaque }))["idempotency_key"],
      opaque,
    );
  },
);

test(
  "submit-intent: a submit never reaches the platform without a key",
  TEST_OPTIONS,
  async () => {
    const order: string[] = [];
    const client = recordingClient(order);
    const bridge = new Bridge(client, { submitIntentStore: new RecordingStore(order) });
    const bare: BridgeRequest = {
      operation: "submit",
      project: PROJECT,
      input: { xyz_input: "CCO" },
    };
    assert.equal(canDeriveSubmitKey(bare), false);
    assert.equal(resolveSubmitIntentIdentity(bare), null);
    assert.equal(canDeriveSubmitKey(submitRequest()), true);
    await assert.rejects(
      () => bridge.execute(bare),
      (error: unknown) => {
        assert.ok(error instanceof BridgeContractError);
        assert.equal(error.error.class, "invalid-parameter");
        assert.deepEqual(error.error.params["fields"], ["idempotencyKey"]);
        return true;
      },
    );
    assert.equal(client.calls.length, 0);
    assert.deepEqual(order, []);
  },
);
