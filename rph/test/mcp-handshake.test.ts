/**
 * M0-09: application-level research-extension handshake (tools/call carrier).
 *
 * Proves that the bridge
 * - sends the one legal request (`research_extension{id,version}`, plus audit
 *   facts) to `acp_capabilities` and never to `initialize`;
 * - reads `extensions[]` and reports the server's advertisement without
 *   upgrading a request into a negotiation;
 * - surfaces a typed in-band refusal (`unsupported_capability` /
 *   `access_denied`) as a value, not an exception, so a supervisor can continue
 *   on P1;
 * - refuses a malformed advertisement locally;
 * - and that the DSH `ctx.tools` adapter reproduces the observed plugin
 *   behaviour (an `isError` result becomes a thrown failure whose message is the
 *   machine envelope) without losing the typed refusal.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  BRIDGE_EXTENSION_ID,
  BRIDGE_EXTENSION_VERSION,
  BRIDGE_MODEL_PREFIX,
  Bridge,
  RESEARCH_HANDSHAKE_TOOL,
  assertOperationAllowed,
  negotiateResearch,
  parseExtensionAdvertisements,
  parseResearchHandshake,
  type BridgeToolResult,
  type PlatformClient,
  type ResearchHandshake,
} from "@rph/bridge";
import {
  platformClientFromToolRuntime,
  type DshToolExecutionRequest,
  type DshToolRuntime,
} from "@rph/dsh-bundle";

import { TEST_OPTIONS } from "./harness.js";

interface RecordedCall {
  readonly name: string;
  readonly params: Readonly<Record<string, unknown>>;
}

interface RecordingClient extends PlatformClient {
  readonly calls: RecordedCall[];
}

function recordingClient(
  handler: (name: string, params: Readonly<Record<string, unknown>>) => BridgeToolResult,
): RecordingClient {
  const calls: RecordedCall[] = [];
  return {
    calls,
    async callTool(name, params) {
      calls.push({ name, params });
      return handler(name, params);
    },
  };
}

/** The server's answer for an advertised-but-unimplemented extension. */
const ADVERTISED_NOT_IMPLEMENTED = {
  schema_version: 1,
  operation: "acp_capabilities",
  status: "error",
  exit_code: 2,
  dry_run: false,
  operation_identity: "tenant:local-single",
  diagnostic_ref: null,
  data: {
    protocol_version: "2025-06-18",
    tools: ["acp_capabilities"],
    extensions: [
      {
        id: BRIDGE_EXTENSION_ID,
        version: BRIDGE_EXTENSION_VERSION,
        supported: true,
        supported_versions: [BRIDGE_EXTENSION_VERSION],
        requested: { id: BRIDGE_EXTENSION_ID, version: BRIDGE_EXTENSION_VERSION },
        negotiated_by: "tools/call:acp_capabilities",
        implemented: false,
        negotiated: false,
        refusal: {
          class: "unsupported",
          code: "unsupported_capability",
          params: { rule: "extension_not_implemented" },
        },
      },
    ],
  },
  error: {
    code: "unsupported_capability",
    message: "research extension request refused",
    params: { rule: "extension_not_implemented" },
    retryable: false,
    diagnostic_ref: null,
  },
};

const NEGOTIATED = {
  schema_version: 1,
  operation: "acp_capabilities",
  status: "ok",
  exit_code: 0,
  dry_run: false,
  operation_identity: "tenant:local-single",
  diagnostic_ref: null,
  data: {
    extensions: [
      {
        id: BRIDGE_EXTENSION_ID,
        version: BRIDGE_EXTENSION_VERSION,
        supported: true,
        supported_versions: [BRIDGE_EXTENSION_VERSION],
        requested: { id: BRIDGE_EXTENSION_ID, version: BRIDGE_EXTENSION_VERSION },
        negotiated_by: "tools/call:acp_capabilities",
        implemented: true,
        negotiated: true,
        refusal: null,
      },
    ],
  },
  error: null,
};

function errorResult(envelope: Readonly<Record<string, unknown>>): BridgeToolResult {
  return { isError: true, structuredContent: envelope };
}

function okResult(envelope: Readonly<Record<string, unknown>>): BridgeToolResult {
  return { isError: false, structuredContent: envelope };
}

function handshakeStatus(handshake: ResearchHandshake): string {
  return handshake.status;
}

test("negotiateResearch calls acp_capabilities with the extension request", TEST_OPTIONS, async () => {
  const client = recordingClient(() => errorResult(ADVERTISED_NOT_IMPLEMENTED));
  const handshake = await negotiateResearch(client, {
    projectId: "proj-demo",
    client: { name: "dsh-mcp-client", version: "0.0.1" },
  });

  assert.equal(client.calls.length, 1);
  assert.equal(client.calls[0]!.name, RESEARCH_HANDSHAKE_TOOL);
  assert.equal(client.calls[0]!.name, "acp_capabilities");
  assert.deepEqual(client.calls[0]!.params, {
    research_extension: {
      id: BRIDGE_EXTENSION_ID,
      version: BRIDGE_EXTENSION_VERSION,
    },
    project_id: "proj-demo",
    client: { name: "dsh-mcp-client", version: "0.0.1" },
  });

  // Advertised, exact version, implemented=false -> a typed refusal, no claim.
  assert.equal(handshakeStatus(handshake), "refused");
  assert.equal(handshake.negotiatedExtensions.length, 0);
  assert.equal(handshake.refusal?.code, "unsupported_capability");
  assert.equal(handshake.refusal?.params["rule"], "extension_not_implemented");
  assert.equal(handshake.extension?.implemented, false);
  assert.equal(handshake.extension?.negotiated, false);
  assert.equal(handshake.extension?.negotiatedBy, "tools/call:acp_capabilities");
  assert.deepEqual(handshake.requested, {
    id: BRIDGE_EXTENSION_ID,
    version: BRIDGE_EXTENSION_VERSION,
  });
});

test("a version mismatch is surfaced, never silently accepted", TEST_OPTIONS, async () => {
  const client = recordingClient(() => errorResult(ADVERTISED_NOT_IMPLEMENTED));
  const handshake = await negotiateResearch(client, {
    version: "1.1.0",
  });

  const requested = client.calls[0]!.params["research_extension"] as
    | Readonly<Record<string, unknown>>
    | undefined;
  assert.deepEqual(requested, { id: BRIDGE_EXTENSION_ID, version: "1.1.0" });
  assert.equal(handshake.status, "refused");
  assert.equal(handshake.negotiatedExtensions.length, 0);
  // The bridge never guesses a compatible shape when versions disagree.
  assert.notEqual(handshake.extension?.version, "1.1.0");
});

test("a fully implemented advertisement is the only negotiated outcome", TEST_OPTIONS, async () => {
  const client = recordingClient(() => okResult(NEGOTIATED));
  const handshake = await negotiateResearch(client);

  assert.equal(handshake.status, "negotiated");
  assert.deepEqual(handshake.negotiatedExtensions, [
    { id: BRIDGE_EXTENSION_ID, version: BRIDGE_EXTENSION_VERSION },
  ]);
  assert.equal(handshake.refusal, null);
  // The negotiated set is exactly what `Bridge` expects for P2 calls.
  assert.doesNotThrow(() =>
    assertOperationAllowed("proposal.create", handshake.negotiatedExtensions),
  );
});

test("an advertised-but-not-negotiated extension cannot unlock P2", TEST_OPTIONS, async () => {
  const client = recordingClient(() => errorResult(ADVERTISED_NOT_IMPLEMENTED));
  const handshake = await negotiateResearch(client);

  assert.equal(handshake.negotiatedExtensions.length, 0);
  assert.throws(
    () => assertOperationAllowed("proposal.create", handshake.negotiatedExtensions),
    /unsupported:unsupported_capability/,
  );
  const bridge = new Bridge(client, {
    negotiatedExtensions: handshake.negotiatedExtensions,
  });
  await assert.rejects(
    bridge.executeP2("proposal.create", { proposal: {} }),
    /unsupported:unsupported_capability/,
  );
});

test("a missing advertisement is 'absent', not a silent success", TEST_OPTIONS, () => {
  const handshake = parseResearchHandshake(okResult({ data: { extensions: [] } }));
  assert.equal(handshake.status, "absent");
  assert.equal(handshake.extension, null);
  assert.equal(handshake.refusal?.code, "unsupported_capability");
  assert.equal(handshake.refusal?.params["rule"], "extension_not_advertised");
});

test("only extensions[] is read; a non-array projection is refused locally", TEST_OPTIONS, () => {
  const assignments = parseExtensionAdvertisements({
    data: { extensions: [{ id: BRIDGE_EXTENSION_ID, version: "1.0.0" }] },
  });
  assert.equal(assignments.length, 1);
  assert.equal(assignments[0]!.implemented, false);
  assert.deepEqual(parseExtensionAdvertisements({ data: {} }), []);
  assert.deepEqual(parseExtensionAdvertisements({}), []);

  assert.throws(
    () => parseExtensionAdvertisements({ data: { extensions: "none" } }),
    /invalid-parameter:bridge_payload_invalid/,
  );
  assert.throws(
    () => parseExtensionAdvertisements({ data: { extensions: [{ id: "" }] } }),
    /invalid-parameter:bridge_payload_invalid/,
  );
});

test("an access-denied handshake keeps its authorization class", TEST_OPTIONS, async () => {
  const client = recordingClient(() =>
    errorResult({
      status: "error",
      data: { extensions: [] },
      error: {
        code: "access_denied",
        message: "requested project does not match the server binding",
        params: { rule: "project_scope_mismatch" },
      },
    }),
  );
  const handshake = await negotiateResearch(client, { projectId: "other-project" });
  assert.equal(handshake.status, "absent");
  assert.equal(handshake.refusal?.class, "authorization");
  assert.equal(handshake.refusal?.params["rule"], "project_scope_mismatch");
});

test("a transport failure is retryable, not a fake negotiation", TEST_OPTIONS, async () => {
  const failing: PlatformClient = {
    async callTool() {
      throw new Error("MCP connection closed");
    },
  };
  const handshake = await negotiateResearch(failing);
  assert.equal(handshake.status, "refused");
  assert.equal(handshake.refusal?.class, "retryable");
  assert.equal(handshake.refusal?.params["rule"], "handshake_call_failed");
  assert.equal(handshake.negotiatedExtensions.length, 0);
});

// ---------------------------------------------------------------------------
// DSH `ctx.tools` adapter
// ---------------------------------------------------------------------------

interface RecordedExecution extends DshToolExecutionRequest {}

function fakeToolRuntime(
  outcome: (request: DshToolExecutionRequest) => Promise<unknown>,
): DshToolRuntime & { readonly seen: RecordedExecution[] } {
  const seen: RecordedExecution[] = [];
  return {
    seen,
    async execute(request) {
      seen.push(request);
      return outcome(request);
    },
  } as DshToolRuntime & { readonly seen: RecordedExecution[] };
}

test("the ctx.tools adapter maps raw names to the DSH model-visible name", TEST_OPTIONS, async () => {
  const runtime = fakeToolRuntime(async () => ({
    isError: false,
    value: {
      content: [{ type: "text", text: "{}" }],
      structuredContent: NEGOTIATED,
    },
  }));
  const client = platformClientFromToolRuntime(runtime);
  const handshake = await negotiateResearch(client);

  assert.equal(runtime.seen.length, 1);
  assert.equal(runtime.seen[0]!.name, `${BRIDGE_MODEL_PREFIX}acp_capabilities`);
  assert.deepEqual(runtime.seen[0]!.arguments, {
    research_extension: {
      id: BRIDGE_EXTENSION_ID,
      version: BRIDGE_EXTENSION_VERSION,
    },
  });
  assert.equal(runtime.seen[0]!.callId, "rph-acp-1");
  assert.equal(typeof runtime.seen[0]!.signal, "object");
  assert.equal(handshake.status, "negotiated");
});

test("the adapter recovers the typed refusal DSH loses in a thrown failure", TEST_OPTIONS, async () => {
  // Observed plugin behaviour: an MCP isError result throws with the envelope
  // JSON as the message (structuredContent is dropped).
  const runtime = fakeToolRuntime(async () => {
    throw new Error(JSON.stringify(ADVERTISED_NOT_IMPLEMENTED));
  });
  const handshake = await negotiateResearch(platformClientFromToolRuntime(runtime));

  assert.equal(handshake.status, "refused");
  assert.equal(handshake.refusal?.code, "unsupported_capability");
  assert.equal(handshake.refusal?.params["rule"], "extension_not_implemented");
  assert.equal(handshake.extension?.implemented, false);
});

test("the adapter also recovers the envelope from a DSH failure value", TEST_OPTIONS, async () => {
  const runtime = fakeToolRuntime(async () => ({
    isError: true,
    error: { message: JSON.stringify(ADVERTISED_NOT_IMPLEMENTED) },
    content: [],
  }));
  const handshake = await negotiateResearch(platformClientFromToolRuntime(runtime));
  assert.equal(handshake.status, "refused");
  assert.equal(handshake.refusal?.params["rule"], "extension_not_implemented");
});

test("an unrecoverable failure stays a typed backend_failure", TEST_OPTIONS, async () => {
  const runtime = fakeToolRuntime(async () => {
    throw new Error("tool registry exploded");
  });
  const client = platformClientFromToolRuntime(runtime);
  const result = await client.callTool("acp_capabilities", {});
  assert.equal(result.isError, true);
  const error = result.structuredContent["error"] as Readonly<Record<string, unknown>>;
  assert.equal(error["code"], "backend_failure");
  assert.equal(
    (error["params"] as Readonly<Record<string, unknown>>)["rule"],
    undefined,
  );

  const handshake = await negotiateResearch(client);
  assert.equal(handshake.status, "absent");
});

test("the adapter honours a caller-owned signal and name mapper", TEST_OPTIONS, async () => {
  const controller = new AbortController();
  const runtime = fakeToolRuntime(async () => ({
    isError: false,
    value: { structuredContent: NEGOTIATED },
  }));
  const client = platformClientFromToolRuntime(runtime, {
    signal: controller.signal,
    callIdPrefix: "acceptance",
    resolveToolName: (raw) => `custom__${raw}`,
  });
  await negotiateResearch(client);
  assert.equal(runtime.seen[0]!.signal, controller.signal);
  assert.equal(runtime.seen[0]!.callId, "acceptance-1");
  assert.equal(runtime.seen[0]!.name, "custom__acp_capabilities");
});
