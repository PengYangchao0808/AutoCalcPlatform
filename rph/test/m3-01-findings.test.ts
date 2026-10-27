/**
 * M3-01b — the go/no-go rework findings F2/F4/F5 and the public session adapter.
 *
 * F2: the bridge's strict redaction guard must not lose a legitimate
 *     capabilities/prepare projection.  The restoration step drops exactly the
 *     frozen inert audit member and withholds host paths; a real credential is
 *     never stripped and is still refused.
 * F4: `ready` (platform) and `readable` (experience) are one fact in two
 *     vocabularies; the mapping lives in the bridge and an unknown state is a
 *     typed refusal, never a fifth state.
 * F5: RPH attribution reads the digest-bound TaskRef, so a redacted
 *     `project_id`/`approval_ref` in the response body cannot break it.
 * Adapter: product mode fails closed on a protocol mismatch, an un-negotiated
 *     extension, or an incomplete tool list; the negotiated set comes only from
 *     the server's answer (there is no injection option).
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  Bridge,
  C_AVAILABILITY_STATES,
  BRIDGE_EXTENSION_ID,
  BRIDGE_EXTENSION_VERSION,
  P1_TOOL_NAMES,
  P2_OPERATION_MAP,
  PLATFORM_AVAILABILITY_STATES,
  RESEARCH_HANDSHAKE_TOOL,
  AVAILABILITY_MAP,
  attributedSubmitBinding,
  availabilityOf,
  mapAvailability,
  parseResearchHandshake,
  projectToolResult,
  type BridgeToolResult,
} from "@rph/bridge";
import {
  HOST_PATH_PLACEHOLDER,
  INERT_AUDIT_POINTER,
  MCP_PROTOCOL_VERSION,
  McpStdioClient,
  PlatformSessionError,
  REQUIRED_P2_TOOL_NAMES,
  buildPlatformSession,
  openMcpSession,
  platformClientFromMcp,
  platformClientFromToolRuntime,
  restorePlatformResult,
  withResultRestoration,
  type McpTransport,
} from "@rph/dsh-bundle";

import { TEST_OPTIONS } from "./harness.js";

const PROJECT_ID = "6f1c2a9e-4b3d-4f80-9a11-2c7d5e8b0f42";
const APPROVAL_REF = "ap_" + "d".repeat(32);
const RESERVATION_ID = "rsv_" + "e".repeat(32);

/** The real capabilities answer, with the two members the guard used to refuse. */
function capabilitiesEnvelope(options: { negotiated: boolean }): Readonly<Record<string, unknown>> {
  return {
    schema_version: 1,
    operation: "acp_capabilities",
    status: "ok",
    exit_code: 0,
    dry_run: false,
    operation_identity: "tenant:local-single",
    diagnostic_ref: null,
    data: {
      protocol_version: MCP_PROTOCOL_VERSION,
      tools: [...P1_TOOL_NAMES],
      backends: [
        { name: "orca", available: true, executable: "/opt/orca/bin/orca" },
      ],
      extensions: [
        {
          id: BRIDGE_EXTENSION_ID,
          version: BRIDGE_EXTENSION_VERSION,
          supported: true,
          supported_versions: [BRIDGE_EXTENSION_VERSION],
          requested: { id: BRIDGE_EXTENSION_ID, version: BRIDGE_EXTENSION_VERSION },
          negotiated_by: `tools/call:${RESEARCH_HANDSHAKE_TOOL}`,
          implemented: true,
          negotiated: options.negotiated,
          refusal: null,
        },
      ],
      capability_request: {
        client: { name: "rph", version: "0.0.0" },
        project_id: PROJECT_ID,
        research_extension: {
          id: BRIDGE_EXTENSION_ID,
          version: BRIDGE_EXTENSION_VERSION,
        },
        authorization: "none: audit and advisory hints only",
      },
      identity_binding: { project_id: PROJECT_ID, work_root: "/ACPServer/acp2-runs/w" },
    },
    error: null,
  };
}

function toolResult(envelope: Readonly<Record<string, unknown>>): BridgeToolResult {
  return { isError: false, structuredContent: envelope };
}

/** Assert the bridge guard refuses a result with one typed rule. */
function assertRefused(
  result: BridgeToolResult,
  rule: string,
  tool = "acp_capabilities",
): void {
  assert.throws(
    () => projectToolResult(tool, result),
    (error: unknown) => {
      const contract = error as {
        error?: { code?: string; params?: { rule?: string } };
      };
      assert.equal(contract.error?.code, "access_denied");
      assert.equal(contract.error?.params?.["rule"], rule);
      return true;
    },
  );
}

// ---------------------------------------------------------------------------
// F2 — restoration keeps the projection, the guard keeps its teeth
// ---------------------------------------------------------------------------

test("F2: the raw capabilities payload is refused by the strict guard", TEST_OPTIONS, () => {
  // Both a host path and the inert `authorization` member are refused by the
  // strict guard; either rule is a refusal (never a silent pass).
  assert.throws(
    () => projectToolResult("acp_capabilities", toolResult(capabilitiesEnvelope({ negotiated: true }))),
    /access_denied/,
  );
});

test("F2: restoration keeps the capability projection and the handshake", TEST_OPTIONS, () => {
  const restored = restorePlatformResult(toolResult(capabilitiesEnvelope({ negotiated: true })));
  assert.deepEqual(restored.report.dropped_audit_members, [INERT_AUDIT_POINTER]);
  assert.ok(restored.report.withheld_host_paths.length >= 1);
  const text = JSON.stringify(restored.result.structuredContent);
  assert.equal(text.includes("/opt/orca/bin/orca"), false, "host path withheld");
  assert.equal(text.includes("/ACPServer/acp2-runs/w"), false, "work root withheld");
  assert.ok(text.includes(HOST_PATH_PLACEHOLDER));

  // The bridge can now project it, and the handshake is readable.
  const projection = projectToolResult("acp_capabilities", restored.result);
  assert.equal(projection.outcome, "ok");
  const handshake = parseResearchHandshake(restored.result);
  assert.equal(handshake.status, "negotiated");
  assert.equal(handshake.negotiatedExtensions.length, 1);
  const data = restored.result.structuredContent["data"] as Record<string, unknown>;
  assert.deepEqual(data["extensions"], (capabilitiesEnvelope({ negotiated: true })["data"] as Record<string, unknown>)["extensions"]);
});

test("F2: a genuine credential is never restored away", TEST_OPTIONS, () => {
  const leaked = capabilitiesEnvelope({ negotiated: true }) as Record<string, unknown>;
  const data = { ...(leaked["data"] as Record<string, unknown>) };
  data["capability_request"] = {
    ...(data["capability_request"] as Record<string, unknown>),
    authorization: "Bearer abcdefghijklmnop",
  };
  const restored = restorePlatformResult(toolResult({ ...leaked, data }));
  const text = JSON.stringify(restored.result.structuredContent);
  assert.ok(text.includes("Bearer abcdefghijklmnop"), "the credential is left for the guard");
  assert.deepEqual(restored.report.dropped_audit_members, []);
  assertRefused(restored.result, "credential_in_result");
});

test("F2: any other credential-bearing key still refuses", TEST_OPTIONS, () => {
  const envelope = capabilitiesEnvelope({ negotiated: true }) as Record<string, unknown>;
  const data = { ...(envelope["data"] as Record<string, unknown>), access_token: "abc123" };
  const restored = restorePlatformResult(toolResult({ ...envelope, data }));
  assertRefused(restored.result, "credential_in_result");
});

test("F2: a prepare envelope with a host path survives the bridge", TEST_OPTIONS, async () => {
  const envelope = {
    schema_version: 1,
    operation: "acp_prepare_artifact",
    status: "ok",
    data: {
      purpose: "artifact",
      availability: "ready",
      operation_ref: { kind: "operation", id: "op-1" },
      artifact_ref: {
        job_id: "job-1",
        resource_id: "input_preview",
        attempt: 1,
        digest: "sha256:" + "f".repeat(64),
      },
      work_dir: "/ACPServer/acp2-runs/remediation/jobs/job-1",
    },
  };
  const raw: BridgeToolResult = { isError: false, structuredContent: envelope };
  const platform = withResultRestoration({ async callTool() { return raw; } });
  const bridge = new Bridge(platform);
  const prepare = await bridge.execute({
    operation: "prepare",
    task: "job-1",
    attempt: 1,
    resource: "input_preview",
  });
  assert.equal(prepare.isError, false);
  assert.equal(availabilityOf(prepare), "ready");
});

test("F2: the ctx.tools adapter applies the same restoration", TEST_OPTIONS, async () => {
  const client = platformClientFromToolRuntime({
    async execute() {
      return {
        isError: false,
        value: { structuredContent: capabilitiesEnvelope({ negotiated: true }) },
      };
    },
  });
  const handshake = parseResearchHandshake(
    await client.callTool("acp_capabilities", {}),
  );
  assert.equal(handshake.status, "negotiated");
});

test("F2: restoration is idempotent", TEST_OPTIONS, () => {
  const once = restorePlatformResult(toolResult(capabilitiesEnvelope({ negotiated: true })));
  const twice = restorePlatformResult(once.result);
  assert.deepEqual(twice.result.structuredContent, once.result.structuredContent);
  assert.deepEqual(twice.report.dropped_audit_members, []);
  assert.deepEqual(twice.report.withheld_host_paths, []);
});

// ---------------------------------------------------------------------------
// F4 — availability vocabulary
// ---------------------------------------------------------------------------

test("F4: the two vocabularies are mapped one-to-one", TEST_OPTIONS, () => {
  assert.deepEqual([...C_AVAILABILITY_STATES], [
    "unprepared",
    "preparing",
    "readable",
    "failed",
  ]);
  assert.deepEqual([...PLATFORM_AVAILABILITY_STATES], [
    "absent",
    "preparing",
    "ready",
    "unavailable",
  ]);
  assert.deepEqual({ ...AVAILABILITY_MAP }, {
    absent: "unprepared",
    preparing: "preparing",
    ready: "readable",
    unavailable: "failed",
  });
  for (const state of PLATFORM_AVAILABILITY_STATES) {
    assert.ok(C_AVAILABILITY_STATES.includes(mapAvailability(state)), state);
  }
  // A consumer that trusts only the C vocabulary sees every state exactly once.
  assert.equal(new Set(Object.values(AVAILABILITY_MAP)).size, C_AVAILABILITY_STATES.length);
});

test("F4: an unknown availability is a typed refusal, not a fifth state", TEST_OPTIONS, () => {
  const rule = (value: unknown): string | undefined => {
    try {
      mapAvailability(value);
    } catch (error) {
      const contract = error as { error?: { params?: { rule?: string } } };
      return contract.error?.params?.["rule"];
    }
    return undefined;
  };
  assert.equal(rule("available"), "availability_unknown");
  assert.equal(rule(undefined), "availability_unknown");
  assert.equal(rule("readable"), "availability_unknown");
});

// ---------------------------------------------------------------------------
// F5 — attribution is carried by the TaskRef, not by response plaintext
// ---------------------------------------------------------------------------

test("F5: a redacted response body does not break RPH attribution", TEST_OPTIONS, () => {
  const redacted = {
    schema_version: 1,
    status: "ok",
    data: {
      task_ref: {
        task_id: "job-5",
        job_id: "job-5",
        project_id: PROJECT_ID,
        attempt: 1,
        release: { release_id: "rel-1", release_digest: "sha256:" + "c".repeat(64) },
      },
      project_id: "<redacted>",
      approval_ref: "<redacted>",
      reservation_id: "<redacted>",
      idempotency_key_origin: "client",
    },
  };
  const binding = attributedSubmitBinding(toolResult(redacted), {
    project_id: PROJECT_ID,
    research_id: "res-5",
    round: 2,
    approval_ref: APPROVAL_REF,
  });
  assert.equal(binding.project_id, PROJECT_ID);
  assert.equal(binding.research_id, "res-5");
  assert.equal(binding.round, 2);
  assert.equal(binding.approval_ref, APPROVAL_REF, "the approval ref is the caller's record");
  assert.equal(binding.task_ref.task_id, "job-5");
  assert.equal(binding.task_ref.attempt, 1);
});

test("F5: a foreign project in the TaskRef is refused", TEST_OPTIONS, () => {
  const payload = {
    data: {
      task_ref: {
        task_id: "job-6",
        project_id: "proj-other",
        attempt: 1,
        release: { release_id: "rel-1", release_digest: "sha256:" + "c".repeat(64) },
      },
    },
  };
  assert.throws(
    () =>
      attributedSubmitBinding(toolResult(payload), {
        project_id: PROJECT_ID,
        research_id: "res-5",
        round: 0,
      }),
    /foreign_project/,
  );
});

// ---------------------------------------------------------------------------
// Public session adapter: real handshake → real tool list → negotiated bridge
// ---------------------------------------------------------------------------

function toolDescriptors(names: readonly string[]) {
  return names.map((name) => ({ name, description: name, inputSchema: { type: "object" } }));
}

const ALL_P2_TOOL_NAMES = [...new Set(Object.values(P2_OPERATION_MAP).map((m) => m.tool))];

interface SentFrame {
  readonly raw: string;
}

function fakeTransport(
  answer: (frame: Record<string, unknown>) => Record<string, unknown> | null,
): { transport: McpTransport; sent: SentFrame[] } {
  const sent: SentFrame[] = [];
  let onLine: ((line: string) => void) | null = null;
  const transport: McpTransport = {
    send(frame) {
      sent.push({ raw: frame });
      const parsed = JSON.parse(frame) as Record<string, unknown>;
      if (typeof parsed["id"] !== "number") {
        return;
      }
      const reply = answer(parsed);
      if (reply !== null) {
        queueMicrotask(() => onLine?.(JSON.stringify(reply)));
      }
    },
    onLine(handler) {
      onLine = handler;
    },
    onError() {},
    onClose() {},
    close() {},
  };
  return { transport, sent };
}

function serverFor(options: {
  readonly protocolVersion?: string;
  readonly negotiated: boolean;
  readonly tools: readonly string[];
}): McpTransport {
  return fakeTransport((frame) => {
    if (frame["method"] === "initialize") {
      return {
        jsonrpc: "2.0",
        id: frame["id"],
        result: {
          protocolVersion: options.protocolVersion ?? MCP_PROTOCOL_VERSION,
          capabilities: { tools: {} },
          serverInfo: { name: "acp", version: "0.3.0" },
        },
      };
    }
    if (frame["method"] === "tools/list") {
      return {
        jsonrpc: "2.0",
        id: frame["id"],
        result: { tools: toolDescriptors(options.tools) },
      };
    }
    if (frame["method"] === "tools/call") {
      const params = (frame["params"] ?? {}) as Record<string, unknown>;
      if (params["name"] !== RESEARCH_HANDSHAKE_TOOL) {
        const data = { tool: params["name"], status: "ok" };
        return {
          jsonrpc: "2.0",
          id: frame["id"],
          result: {
            isError: false,
            structuredContent: { schema_version: 1, status: "ok", data },
            content: [{ type: "text", text: JSON.stringify(data) }],
          },
        };
      }
      const envelope = capabilitiesEnvelope({ negotiated: options.negotiated });
      return {
        jsonrpc: "2.0",
        id: frame["id"],
        result: {
          isError: false,
          structuredContent: envelope,
          content: [{ type: "text", text: JSON.stringify(envelope) }],
        },
      };
    }
    return null;
  })
  .transport;
}

async function initializedClient(transport: McpTransport): Promise<McpStdioClient> {
  const client = new McpStdioClient(transport);
  await client.initialize();
  return client;
}

test("adapter: a negotiated session binds the bridge to the server's answer", TEST_OPTIONS, async () => {
  const transport = serverFor({
    negotiated: true,
    tools: [...P1_TOOL_NAMES, ...ALL_P2_TOOL_NAMES],
  });
  const client = await initializedClient(transport);
  const session = await openMcpSession(client, { mode: "product" });
  assert.equal(session.protocolVersion, MCP_PROTOCOL_VERSION);
  assert.equal(session.handshake.status, "negotiated");
  assert.deepEqual(session.negotiatedExtensions, [
    { id: BRIDGE_EXTENSION_ID, version: BRIDGE_EXTENSION_VERSION },
  ]);
  assert.equal(session.researchEnabled, true);
  assert.deepEqual([...REQUIRED_P2_TOOL_NAMES].sort(), [...new Set(ALL_P2_TOOL_NAMES)].sort());
  // The real list is injected verbatim (both surfaces present).
  assert.equal(session.toolNames.includes("acp_capabilities"), true);
  assert.equal(session.toolNames.includes("acp_research_wait_v1"), true);
  // P2 is usable on the session's bridge.
  const p2 = await session.bridge.executeP2("research.snapshot", {});
  assert.equal(p2.tool, "acp_research_snapshot_v1");
});

test("adapter: product mode refuses a protocol mismatch", TEST_OPTIONS, async () => {
  const transport = serverFor({
    protocolVersion: "2024-11-05",
    negotiated: true,
    tools: [...P1_TOOL_NAMES, ...ALL_P2_TOOL_NAMES],
  });
  const client = await initializedClient(transport);
  await assert.rejects(
    () => openMcpSession(client, { mode: "product" }),
    (error: unknown) =>
      error instanceof PlatformSessionError && error.code === "protocol_version_mismatch",
  );
});

test("adapter: product mode refuses an un-negotiated P2 extension", TEST_OPTIONS, async () => {
  const transport = serverFor({
    negotiated: false,
    tools: [...P1_TOOL_NAMES, ...ALL_P2_TOOL_NAMES],
  });
  const client = await initializedClient(transport);
  await assert.rejects(
    () => openMcpSession(client, { mode: "product" }),
    (error: unknown) =>
      error instanceof PlatformSessionError &&
      error.code === "research_extension_not_negotiated" &&
      (error.params["status"] as string) === "advertised",
  );
});

test("adapter: acceptance mode records the real answer and keeps P2 locked", TEST_OPTIONS, async () => {
  const transport = serverFor({
    negotiated: false,
    tools: [...P1_TOOL_NAMES],
  });
  const client = await initializedClient(transport);
  const session = await openMcpSession(client, { mode: "acceptance" });
  assert.equal(session.handshake.status, "advertised");
  assert.deepEqual(session.negotiatedExtensions, []);
  assert.equal(session.researchEnabled, false);
  await assert.rejects(
    () => session.bridge.executeP2("proposal.create", {}),
    /unsupported:unsupported_capability/,
  );
});

test("adapter: a tool list that misses the promised surface fails closed", TEST_OPTIONS, () => {
  assert.throws(
    () =>
      buildPlatformSession({
        protocolVersion: MCP_PROTOCOL_VERSION,
        tools: toolDescriptors([...P1_TOOL_NAMES]),
        handshake: parseResearchHandshake(
          toolResult(capabilitiesEnvelope({ negotiated: true })),
        ),
        client: { async callTool() { return toolResult({}); } },
      }),
    (error: unknown) =>
      error instanceof PlatformSessionError &&
      error.code === "tools_list_incomplete" &&
      (error.params["missing"] as string[]).includes("acp_research_wait_v1"),
  );
});

test("adapter: no option can inject a negotiated extension", TEST_OPTIONS, () => {
  const handshake = parseResearchHandshake(
    toolResult(capabilitiesEnvelope({ negotiated: false })),
  );
  assert.deepEqual(handshake.negotiatedExtensions, []);
  const session = buildPlatformSession({
    mode: "acceptance",
    protocolVersion: MCP_PROTOCOL_VERSION,
    tools: toolDescriptors([...P1_TOOL_NAMES, ...ALL_P2_TOOL_NAMES]),
    handshake,
    client: { async callTool() { return toolResult({}); } },
  });
  assert.deepEqual(session.negotiatedExtensions, [], "advertisement is never promoted");
  // A caller passing an extension-like option is ignored (the type has no such
  // field and the builder only reads the handshake).
  const injected = buildPlatformSession({
    mode: "acceptance",
    protocolVersion: MCP_PROTOCOL_VERSION,
    tools: toolDescriptors([...P1_TOOL_NAMES]),
    handshake,
    client: { async callTool() { return toolResult({}); } },
    // @ts-expect-error - the adapter has no extension option, by design
    negotiatedExtensions: [{ id: BRIDGE_EXTENSION_ID, version: BRIDGE_EXTENSION_VERSION }],
  });
  assert.deepEqual(injected.negotiatedExtensions, []);
});

test("adapter: the stdio platform client exposes restore=false only for tests", TEST_OPTIONS, async () => {
  const transport = serverFor({ negotiated: true, tools: [...P1_TOOL_NAMES] });
  const client = await initializedClient(transport);
  const restored = platformClientFromMcp(client);
  const result = await restored.callTool("acp_capabilities", {});
  assert.equal(
    JSON.stringify(result.structuredContent).includes("/opt/orca/bin/orca"),
    false,
  );
  const raw = platformClientFromMcp(client, { restore: false });
  const rawResult = await raw.callTool("acp_capabilities", {});
  assert.equal(
    JSON.stringify(rawResult.structuredContent).includes("/opt/orca/bin/orca"),
    true,
  );
});

test("F5: the submit surface is project/approval aware without the response body", TEST_OPTIONS, async () => {
  const platform = {
    calls: [] as string[],
    async callTool(name: string) {
      this.calls.push(name);
      return {
        isError: false,
        structuredContent: {
          status: "ok",
          data: {
            task_ref: {
              task_id: "job-1",
              project_id: PROJECT_ID,
              attempt: 1,
              release: { release_id: "rel-1", release_digest: "sha256:" + "c".repeat(64) },
            },
            project_id: "<redacted>",
            approval_ref: "<redacted>",
            reservation_id: RESERVATION_ID,
          },
        },
      } satisfies BridgeToolResult;
    },
  };
  const bridge = new Bridge(platform);
  const submit = await bridge.execute({
    operation: "submit",
    project: PROJECT_ID,
    workflow: "fake",
    input: { source: "CCO" },
    method: {},
    planDigest: "sha256:" + "a".repeat(64),
    proposalRef: "prop-1",
    research: "res-1",
    round: 0,
  });
  const binding = attributedSubmitBinding(submit, {
    project_id: PROJECT_ID,
    research_id: "res-1",
    round: 0,
    approval_ref: APPROVAL_REF,
  });
  assert.equal(binding.task_ref.project_id, PROJECT_ID);
  assert.equal(binding.approval_ref, APPROVAL_REF);
  assert.deepEqual(platform.calls, ["acp_submit"]);
});
