/**
 * Minimal **real** MCP stdio client (M1-06 / M3-01a).
 *
 * This is the smallest honest MCP client the research client needs: it speaks
 * the newline-delimited JSON-RPC 2.0 framing the ACP adapter implements
 * (the M1-01/B05 stdio framing module), performs the standard
 * `initialize` / `notifications/initialized` handshake at protocol
 * `2025-06-18`, lists the tools and calls them.
 *
 * It owns **no process**: the transport is injected (`McpTransport`), exactly
 * like the DSH `ctx.tools` adapter.  The named launcher
 * (`mcp-stdio-launch.ts`) is the only place that starts `acp mcp serve`, so the
 * bridge/research packages keep their no-spawn/no-fetch boundary.
 *
 * Every refusal is typed (`McpClientError` with a stable `code`); a JSON-RPC
 * error is never turned into a successful tool result, and a tool `isError`
 * result keeps the server's `structuredContent` untouched.
 */

import type { BridgeToolResult, PlatformClient } from "@rph/bridge";

import {
  withResultRestoration,
  type RestorationSink,
} from "./platform-result.js";
import { BUNDLE_VERSION } from "./version.js";

/** Protocol version this client speaks (the ACP adapter's supported version). */
export const MCP_PROTOCOL_VERSION = "2025-06-18";
export const MCP_CLIENT_NAME = "rph-acp-bridge";
/**
 * The client identity sent in `initialize.clientInfo`.
 *
 * Not a second version constant: it is this package's `package.json` version
 * (M3-02 single-source rule).
 */
export const MCP_CLIENT_VERSION = BUNDLE_VERSION;
export const MCP_DEFAULT_TIMEOUT_MS = 30_000;

/** One bidirectional line transport (stdout/stdin of the MCP child). */
export interface McpTransport {
  /** Send one JSON-RPC frame (the transport appends the newline). */
  send(frame: string): void;
  /** Register the single line handler (one frame per line, no newline). */
  onLine(handler: (line: string) => void): void;
  /** Register the failure handler (read error / premature close). */
  onError(handler: (error: Error) => void): void;
  /** Register the close handler. */
  onClose(handler: (error: Error | null) => void): void;
  /** Close the transport (idempotent). */
  close(): void;
}

/** A typed MCP client refusal (protocol/frame/timeout/remote error). */
export class McpClientError extends Error {
  readonly code: string;
  readonly data: Readonly<Record<string, unknown>>;

  constructor(code: string, message: string, data: Readonly<Record<string, unknown>> = {}) {
    super(`${code}: ${message}`);
    this.name = "McpClientError";
    this.code = code;
    this.data = data;
  }
}

/** The `tools/call` result this client exposes (MCP result shape). */
export interface McpToolCallResult {
  readonly isError: boolean;
  readonly structuredContent: Readonly<Record<string, unknown>>;
  readonly contentText: string;
}

export interface McpToolDescriptor {
  readonly name: string;
  readonly description: string;
  readonly inputSchema: Readonly<Record<string, unknown>>;
}

interface Pending {
  readonly resolve: (value: unknown) => void;
  readonly reject: (error: Error) => void;
  readonly timer: ReturnType<typeof setTimeout>;
  readonly method: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function textOf(content: unknown): string {
  if (!Array.isArray(content)) {
    return "";
  }
  const parts: string[] = [];
  for (const item of content) {
    if (isRecord(item) && item["type"] === "text" && typeof item["text"] === "string") {
      parts.push(item["text"]);
    }
  }
  return parts.join("\n");
}

/**
 * One MCP stdio session over an injected line transport.
 *
 * The client is single-connection: `initialize` runs once, every request is
 * matched by its numeric id and every outstanding request is failed once the
 * transport closes.  It holds no task state and caches no tool result.
 */
export class McpStdioClient {
  private readonly transport: McpTransport;
  private readonly timeoutMs: number;
  private readonly pending = new Map<number, Pending>();
  private nextId = 1;
  private initialized = false;
  private closed = false;
  private closeError: Error | null = null;
  private negotiatedProtocolVersion: string | null = null;
  private serverInformation: Readonly<Record<string, unknown>> | null = null;

  constructor(transport: McpTransport, options: { readonly timeoutMs?: number } = {}) {
    this.transport = transport;
    this.timeoutMs = options.timeoutMs ?? MCP_DEFAULT_TIMEOUT_MS;
    this.transport.onLine((line) => this.handleLine(line));
    this.transport.onError((error) => this.failAll(error));
    this.transport.onClose((error) => {
      this.closed = true;
      this.closeError = error;
      this.failAll(error ?? new McpClientError("mcp_closed", "the MCP transport closed"));
    });
  }

  /** True after a successful `initialize` answer. */
  get isInitialized(): boolean {
    return this.initialized;
  }

  /** The protocol version the server answered (`null` before initialize). */
  get protocolVersion(): string | null {
    return this.negotiatedProtocolVersion;
  }

  /** The server's `initialize.serverInfo` (never derived from configuration). */
  get serverInfo(): Readonly<Record<string, unknown>> | null {
    return this.serverInformation;
  }

  /** Run the standard handshake; returns the server's initialize result. */
  async initialize(
    params: Readonly<Record<string, unknown>> = {},
  ): Promise<Readonly<Record<string, unknown>>> {
    if (this.initialized) {
      throw new McpClientError("already_initialized", "this session is already initialized");
    }
    const result = await this.request("initialize", {
      protocolVersion: MCP_PROTOCOL_VERSION,
      capabilities: {},
      clientInfo: { name: MCP_CLIENT_NAME, version: MCP_CLIENT_VERSION },
      ...params,
    });
    if (!isRecord(result)) {
      throw new McpClientError("invalid_initialize", "initialize did not return an object");
    }
    const version = result["protocolVersion"];
    this.negotiatedProtocolVersion = typeof version === "string" ? version : null;
    this.serverInformation = isRecord(result["serverInfo"]) ? result["serverInfo"] : null;
    this.initialized = true;
    this.transport.send(
      JSON.stringify({ jsonrpc: "2.0", method: "notifications/initialized", params: {} }),
    );
    return result;
  }

  /** `tools/list` — the advertised surface (allowlist, never inferred). */
  async listTools(): Promise<readonly McpToolDescriptor[]> {
    this.requireInitialized("tools/list");
    const result = await this.request("tools/list", {});
    const tools = isRecord(result) ? result["tools"] : undefined;
    if (!Array.isArray(tools)) {
      throw new McpClientError("invalid_tools_list", "tools/list did not return a tools array");
    }
    return Object.freeze(
      tools.map((entry) => {
        if (!isRecord(entry) || typeof entry["name"] !== "string") {
          throw new McpClientError("invalid_tools_list", "a tool entry has no name");
        }
        return Object.freeze({
          name: entry["name"],
          description: typeof entry["description"] === "string" ? entry["description"] : "",
          inputSchema: isRecord(entry["inputSchema"]) ? entry["inputSchema"] : {},
        });
      }),
    );
  }

  /** `tools/call` — returns the server's result verbatim (never rewritten). */
  async callTool(
    name: string,
    args: Readonly<Record<string, unknown>> = {},
  ): Promise<McpToolCallResult> {
    this.requireInitialized("tools/call");
    if (typeof name !== "string" || name.length === 0) {
      throw new McpClientError("invalid_tool_name", "a tool name is required");
    }
    const result = await this.request("tools/call", { name, arguments: { ...args } });
    if (!isRecord(result)) {
      throw new McpClientError("invalid_tool_result", `${name} did not return an object`);
    }
    const structured = result["structuredContent"];
    return Object.freeze({
      isError: result["isError"] === true,
      structuredContent: isRecord(structured) ? structured : {},
      contentText: textOf(result["content"]),
    });
  }

  /** Close the session (idempotent; outstanding requests fail). */
  close(): void {
    if (this.closed) {
      return;
    }
    this.closed = true;
    this.failAll(this.closeError ?? new McpClientError("mcp_closed", "the client was closed"));
    this.transport.close();
  }

  // -- internals ---------------------------------------------------------

  private requireInitialized(method: string): void {
    if (this.closed) {
      throw new McpClientError("mcp_closed", `cannot call ${method} on a closed session`);
    }
    if (!this.initialized) {
      throw new McpClientError(
        "not_initialized",
        `${method} requires a successful initialize (MCP lifecycle)`,
      );
    }
  }

  private request(method: string, params: Readonly<Record<string, unknown>>): Promise<unknown> {
    if (this.closed) {
      return Promise.reject(
        new McpClientError("mcp_closed", `cannot send ${method} on a closed session`),
      );
    }
    const id = this.nextId;
    this.nextId += 1;
    return new Promise<unknown>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(
          new McpClientError("mcp_timeout", `${method} exceeded ${this.timeoutMs}ms`, { method }),
        );
      }, this.timeoutMs);
      if (typeof timer === "object" && "unref" in timer) {
        timer.unref();
      }
      this.pending.set(id, { resolve, reject, timer, method });
      try {
        this.transport.send(JSON.stringify({ jsonrpc: "2.0", id, method, params }));
      } catch (error) {
        clearTimeout(timer);
        this.pending.delete(id);
        reject(
          new McpClientError(
            "mcp_transport_failure",
            error instanceof Error ? error.message : String(error),
            { method },
          ),
        );
      }
    });
  }

  private handleLine(line: string): void {
    const trimmed = line.trim();
    if (trimmed.length === 0) {
      return;
    }
    let frame: unknown;
    try {
      frame = JSON.parse(trimmed);
    } catch (error) {
      this.failAll(
        new McpClientError("mcp_frame_invalid", `unreadable MCP frame: ${String(error)}`),
      );
      return;
    }
    if (!isRecord(frame)) {
      this.failAll(new McpClientError("mcp_frame_invalid", "an MCP frame must be an object"));
      return;
    }
    const id = frame["id"];
    if (typeof id !== "number") {
      // Server-initiated notification: this client registers none, so it is
      // recorded nowhere and never interpreted as a tool result.
      return;
    }
    const pending = this.pending.get(id);
    if (pending === undefined) {
      return;
    }
    this.pending.delete(id);
    clearTimeout(pending.timer);
    const error = frame["error"];
    if (isRecord(error)) {
      pending.reject(
        new McpClientError(
          String(error["code"] ?? "mcp_error"),
          typeof error["message"] === "string" ? error["message"] : "MCP error",
          isRecord(error["data"]) ? error["data"] : {},
        ),
      );
      return;
    }
    pending.resolve(frame["result"]);
  }

  private failAll(error: Error): void {
    for (const [id, pending] of this.pending) {
      this.pending.delete(id);
      clearTimeout(pending.timer);
      pending.reject(error);
    }
  }
}

/**
 * Adapt one MCP client into the bridge's `PlatformClient`.
 *
 * A JSON-RPC/protocol failure becomes a typed machine envelope
 * (`code=backend_failure`, `params.rule=protocol_error`) instead of an
 * exception, so the bridge's typed refusal path still owns the decision.  A
 * tool `isError` result keeps the server's `structuredContent` untouched.
 *
 * M3-01b (F2): between the verbatim transport result and the bridge's strict
 * redaction guard sits exactly one restoration step
 * (`restorePlatformResult`): it drops the frozen inert audit member and
 * replaces absolute host paths with `<host-path>`, so a legitimate
 * capabilities/prepare projection survives while a credential still refuses.
 * Pass `restore: false` only in a test that asserts the raw shape.
 */
export function platformClientFromMcp(
  client: McpStdioClient,
  options: {
    readonly restore?: boolean;
    readonly onRestore?: RestorationSink;
  } = {},
): PlatformClient {
  const transport: PlatformClient = {
    async callTool(
      rawName: string,
      params: Readonly<Record<string, unknown>>,
    ): Promise<BridgeToolResult> {
      let result: McpToolCallResult;
      try {
        result = await client.callTool(rawName, params);
      } catch (error) {
        const mcpError = error instanceof McpClientError ? error : null;
        return {
          isError: true,
          structuredContent: {
            schema_version: 1,
            operation: rawName,
            status: "error",
            error: {
              code: mcpError === null ? "backend_failure" : "backend_unavailable",
              message: error instanceof Error ? error.message : String(error),
              params: {
                rule: "protocol_error",
                tool: rawName,
                ...(mcpError === null ? {} : { mcp_code: mcpError.code, ...mcpError.data }),
              },
              retryable: mcpError?.code === "mcp_timeout",
            },
          },
        };
      }
      const structured =
        Object.keys(result.structuredContent).length > 0 || result.contentText.length === 0
          ? result.structuredContent
          : { text: result.contentText };
      return { isError: result.isError, structuredContent: structured };
    },
  };
  return options.restore === false
    ? transport
    : withResultRestoration(transport, options.onRestore);
}
