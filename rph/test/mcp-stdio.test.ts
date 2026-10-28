/**
 * M1-06 / M3-01a: the minimal real MCP stdio client and the bridge research
 * binding it feeds.
 *
 * Proves, without any process or network:
 * - the standard `initialize` / `notifications/initialized` handshake at
 *   2025-06-18 and a `tools/list` / `tools/call` round trip over an injected
 *   line transport;
 * - a JSON-RPC error becomes a typed `McpClientError` and a protocol failure
 *   becomes a typed machine envelope (never a fake success);
 * - the bridge forwards the approved research binding (`plan_digest`,
 *   `proposal_ref`, `proposal_digest`, budget) on submit, so a research launch
 *   can never silently degrade into a legacy submit;
 * - the same raw submit key / intent identity replays the same TaskRef and a
 *   changed plan digest with the same key conflicts locally.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { Bridge, type BridgeRequest } from "@rph/bridge";
import {
  MCP_PROTOCOL_VERSION,
  McpClientError,
  McpStdioClient,
  platformClientFromMcp,
  type McpTransport,
} from "@rph/dsh-bundle";

interface SentFrame {
  readonly raw: string;
}

/** In-memory transport: records sent frames and answers by request id. */
function fakeTransport(
  answer: (frame: Record<string, unknown>) => Record<string, unknown> | Error | null,
): { transport: McpTransport; sent: SentFrame[] } {
  const sent: SentFrame[] = [];
  let onLine: ((line: string) => void) | null = null;
  const transport: McpTransport = {
    send(frame) {
      sent.push({ raw: frame });
      const parsed = JSON.parse(frame) as Record<string, unknown>;
      if (typeof parsed["id"] !== "number") {
        return; // a notification
      }
      const reply = answer(parsed);
      if (reply === null) {
        return;
      }
      if (reply instanceof Error) {
        queueMicrotask(() => {
          onLine?.(JSON.stringify({ jsonrpc: "2.0", id: parsed["id"], error: { code: -32000, message: reply.message } }));
        });
        return;
      }
      queueMicrotask(() => onLine?.(JSON.stringify(reply)));
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

function initializeResult(): Record<string, unknown> {
  return {
    jsonrpc: "2.0",
    id: 1,
    result: {
      protocolVersion: MCP_PROTOCOL_VERSION,
      capabilities: { tools: {} },
      serverInfo: { name: "acp", version: "0.3.0" },
    },
  };
}

test("initialize sends the standard handshake and the initialized notification", async () => {
  const { transport, sent } = fakeTransport((frame) =>
    frame["method"] === "initialize" ? initializeResult() : null,
  );
  const client = new McpStdioClient(transport);
  const result = await client.initialize();
  assert.equal(result["protocolVersion"], MCP_PROTOCOL_VERSION);
  assert.equal(client.isInitialized, true);
  const frames = sent.map((item) => JSON.parse(item.raw) as Record<string, unknown>);
  assert.equal(frames[0]?.["method"], "initialize");
  assert.deepEqual(frames[0]?.["params"], {
    protocolVersion: MCP_PROTOCOL_VERSION,
    capabilities: {},
    clientInfo: { name: "rph-acp-bridge", version: "0.0.0" },
  });
  assert.equal(frames[1]?.["method"], "notifications/initialized");
  assert.equal(Object.hasOwn(frames[0]?.["params"] as object, "extensions"), false);
});

test("tools/list and tools/call are real round trips; structuredContent is untouched", async () => {
  let callId = 0;
  const { transport } = fakeTransport((frame) => {
    if (frame["method"] === "initialize") {
      return initializeResult();
    }
    if (frame["method"] === "tools/list") {
      return {
        jsonrpc: "2.0",
        id: frame["id"],
        result: {
          tools: [
            { name: "acp_submit", description: "submit", inputSchema: { type: "object" } },
          ],
        },
      };
    }
    if (frame["method"] === "tools/call") {
      callId += 1;
      return {
        jsonrpc: "2.0",
        id: frame["id"],
        result: {
          isError: false,
          structuredContent: {
            schema_version: 1,
            operation: "acp_submit",
            status: "ok",
            data: { task_ref: { task_id: "job-1", attempt: 1 }, call: callId },
          },
          content: [{ type: "text", text: "{}" }],
        },
      };
    }
    return null;
  });
  const client = new McpStdioClient(transport);
  await client.initialize();
  const tools = await client.listTools();
  assert.deepEqual(
    tools.map((tool) => tool.name),
    ["acp_submit"],
  );
  const result = await client.callTool("acp_submit", { workflow: "fake" });
  assert.equal(result.isError, false);
  const data = result.structuredContent["data"] as Record<string, unknown>;
  assert.equal((data["task_ref"] as Record<string, unknown>)["task_id"], "job-1");
});

test("a JSON-RPC error and a protocol failure are typed, never a success", async () => {
  const { transport } = fakeTransport((frame) =>
    frame["method"] === "initialize" ? new Error("boom") : null,
  );
  const client = new McpStdioClient(transport);
  await assert.rejects(
    () => client.initialize(),
    (error: unknown) => error instanceof McpClientError && error.code === "-32000",
  );

  const closedTransport: McpTransport = {
    send() {
      throw new Error("child is gone");
    },
    onLine() {},
    onError() {},
    onClose() {},
    close() {},
  };
  const closed = new McpStdioClient(closedTransport);
  const platform = platformClientFromMcp(closed);
  const result = await platform.callTool("acp_submit", {});
  assert.equal(result.isError, true);
  const error = result.structuredContent["error"] as Record<string, unknown>;
  assert.equal(error["code"], "backend_unavailable");
  const params = error["params"] as Record<string, unknown>;
  assert.equal(params["rule"], "protocol_error");
});

function submitRequest(overrides: Partial<BridgeRequest> = {}): BridgeRequest {
  return {
    operation: "submit",
    project: "proj-m106",
    workflow: "fake",
    input: { source: "CCO", source_type: "smiles" },
    method: {},
    resources: { cores: 1 },
    planDigest: "sha256:" + "a".repeat(64),
    proposalRef: "prop-m106",
    proposalDigest: "sha256:" + "b".repeat(64),
    budget: { unit: "task_count", count: 1, timeout_s: 120 },
    research: "res-m106",
    round: 0,
    ...overrides,
  };
}

function recordingPlatform() {
  const calls: { name: string; params: Readonly<Record<string, unknown>> }[] = [];
  return {
    calls,
    client: {
      async callTool(name: string, params: Readonly<Record<string, unknown>>) {
        calls.push({ name, params });
        return {
          isError: false,
          structuredContent: {
            schema_version: 1,
            status: "ok",
            data: {
              task_ref: { task_id: "job-m106", attempt: 1 },
              idempotent_replay: calls.length > 1,
            },
          },
        };
      },
    },
  };
}

test("the bridge forwards the approved research binding on submit", async () => {
  const platform = recordingPlatform();
  const bridge = new Bridge(platform.client);
  const first = await bridge.execute(submitRequest());
  assert.equal(first.isError, false);
  const sent = platform.calls[0];
  assert.equal(sent?.name, "acp_submit");
  assert.equal(sent?.params["plan_digest"], "sha256:" + "a".repeat(64));
  assert.equal(sent?.params["proposal_ref"], "prop-m106");
  assert.equal(sent?.params["proposal_digest"], "sha256:" + "b".repeat(64));
  assert.deepEqual(sent?.params["budget"], { unit: "task_count", count: 1, timeout_s: 120 });
  const key = String(sent?.params["idempotency_key"] ?? "");
  assert.ok(key.length > 0, "the M1-02 intent ledger derived a key");

  const replay = await bridge.execute(submitRequest());
  assert.equal(platform.calls.length, 1, "a replay never calls the platform twice");
  assert.deepEqual(replay.structuredContent, first.structuredContent);
});

test("a changed plan digest with the same research round is a typed conflict", async () => {
  const platform = recordingPlatform();
  const bridge = new Bridge(platform.client);
  await bridge.execute(submitRequest());
  await assert.rejects(
    () =>
      bridge.execute(
        submitRequest({ planDigest: "sha256:" + "c".repeat(64) }),
      ),
    (error: unknown) => {
      const bridgeError = error as { error?: { code?: string } };
      return bridgeError.error?.code === "revision_conflict";
    },
  );
});
