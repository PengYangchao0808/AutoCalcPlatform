/**
 * DSH bundle surface.
 *
 * Only public DSH interface *strings* are named here. DSH itself is installed
 * outside this workspace at `$ACP2_TOOLS/deepseek-harness`; this package must
 * never import DSH private source paths or vendor a DSH fork.
 */

import type { BridgeToolResult, PlatformClient } from "@rph/bridge";
import {
  BRIDGE_OPERATIONS,
  toModelToolName,
  type BridgeOperation,
} from "@rph/bridge";

/** Public DSH MCP client plugin package name (official interface). */
export const DSH_MCP_CLIENT_PLUGIN = "@deepseek-ai/dsh-mcp-client";

/** Public DSH profile names used by the experience client. */
export const DSH_PROFILE_WEB = "web";
export const DSH_PROFILE_HEADLESS = "headless";

/** Public profile union. */
export type BundleProfileName =
  | typeof DSH_PROFILE_WEB
  | typeof DSH_PROFILE_HEADLESS;

/** All profiles this bundle knows how to describe. */
export const BUNDLE_PROFILE_NAMES: readonly BundleProfileName[] = Object.freeze([
  DSH_PROFILE_WEB,
  DSH_PROFILE_HEADLESS,
]);

/** Declarative bundle profile; no DSH private class is referenced. */
export interface BundleProfile {
  readonly name: BundleProfileName;
  readonly plugin: typeof DSH_MCP_CLIENT_PLUGIN;
  readonly bridgeOperations: readonly BridgeOperation[];
  readonly localOnly: boolean;
}

/**
 * Describe one bundle profile.
 *
 * `web` binds loopback only; `headless` is for CI. Both use the same official
 * plugin package and the same bridge operation surface.
 */
export function bundleProfile(name: BundleProfileName): BundleProfile {
  if (name !== DSH_PROFILE_WEB && name !== DSH_PROFILE_HEADLESS) {
    throw new TypeError(`unknown DSH profile: ${String(name)}`);
  }
  return Object.freeze({
    name,
    plugin: DSH_MCP_CLIENT_PLUGIN,
    bridgeOperations: Object.freeze([...BRIDGE_OPERATIONS]),
    localOnly: true,
  });
}

// Research skills and the single Supervisor live at the package root
// (`skills/`) so the contract is readable outside `src/`.
export * from "../skills/index.js";

// ---------------------------------------------------------------------------
// M0-09: `ctx.tools` adapter for the application-level handshake
// ---------------------------------------------------------------------------

/**
 * Structural view of DSH's public `ctx.tools` runtime.
 *
 * Deliberately local and structural: this package never imports DSH private
 * source (boundary gate) — the shape below is the public
 * `ToolRuntime.execute(ToolExecutionInput)` contract
 * (`{name, arguments, callId, signal}`), which the real plugin runtime also
 * satisfies.
 */
export interface DshToolExecutionRequest {
  readonly name: string;
  readonly arguments: unknown;
  readonly callId: string;
  /** Required by the DSH runtime; a caller-owned cancellation token. */
  readonly signal: AbortSignal;
}

/** The minimum of `ctx.tools` this adapter uses. */
export interface DshToolRuntime {
  execute(request: DshToolExecutionRequest): Promise<unknown>;
}

/** Adapter options: cancellation, call-id namespace and raw→DSH name mapping. */
export interface DshToolAdapterOptions {
  readonly signal?: AbortSignal;
  readonly callIdPrefix?: string;
  /**
   * Maps a raw platform tool name to the name DSH registered.
   *
   * DSH registers MCP tools as `mcp__<serverName>__<rawName>`
   * (`publicToolName` in the official plugin) and `ctx.tools.execute` looks the
   * call up by that model-visible name, so the default adds the ACP prefix
   * (`mcp__acp__`). A profile with another `serverName` passes its own mapper.
   */
  readonly resolveToolName?: (raw: string) => string;
}

const DEFAULT_CALL_ID_PREFIX = "rph-acp";

function isRecord(value: unknown): value is Readonly<Record<string, unknown>> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * Recover a machine envelope from a thrown MCP tool failure.
 *
 * Observed DSH behaviour (`@deepseek-ai/dsh-mcp-client` 0.2.1-rc.2 on
 * `@modelcontextprotocol/client` 2.2.0): an MCP `isError: true` result is
 * converted into a **thrown** tool failure whose message is the text content of
 * the result. The ACP adapter puts the whole machine envelope
 * (`MachineResult.to_json()`) into that text, so the typed refusal
 * (`unsupported_capability` / `access_denied` + params) survives as JSON text
 * even though `structuredContent` is dropped by the plugin. Recovering it here
 * is what keeps the handshake refusal typed end to end.
 */
function recoverEnvelope(message: unknown): Readonly<Record<string, unknown>> | null {
  if (typeof message !== "string") {
    return null;
  }
  const trimmed = message.trim();
  if (!trimmed.startsWith("{")) {
    return null;
  }
  try {
    const parsed: unknown = JSON.parse(trimmed);
    if (!isRecord(parsed)) {
      return null;
    }
    const nested = parsed["structuredContent"];
    if (isRecord(nested)) {
      return nested;
    }
    return "error" in parsed || "status" in parsed ? parsed : null;
  } catch {
    return null;
  }
}

function failureMessage(result: Readonly<Record<string, unknown>>): unknown {
  const error = result["error"];
  if (isRecord(error) && typeof error["message"] === "string") {
    return error["message"];
  }
  return undefined;
}

function failureResult(
  tool: string,
  message: unknown,
): BridgeToolResult {
  const recovered = recoverEnvelope(message);
  if (recovered !== null) {
    return { isError: true, structuredContent: recovered };
  }
  return Object.freeze({
    isError: true,
    structuredContent: Object.freeze({
      schema_version: 1,
      operation: tool,
      status: "error",
      error: Object.freeze({
        code: "backend_failure",
        message:
          typeof message === "string"
            ? message
            : "tool call failed without a machine envelope",
        params: Object.freeze({ tool, surface: "ctx.tools.execute" }),
        retryable: false,
      }),
    }),
  }) as BridgeToolResult;
}

/**
 * Adapt one DSH `ctx.tools` runtime into the bridge's `PlatformClient`.
 *
 * Success keeps the MCP result's `structuredContent` intact; an MCP `isError`
 * result (which DSH turns into a thrown failure) is converted back into
 * `{isError: true, structuredContent}` so the bridge can read the typed
 * `error.code`/`error.params`. No fetch, no spawn, no second transport: the
 * runtime is injected by the DSH host.
 */
export function platformClientFromToolRuntime(
  runtime: DshToolRuntime,
  options: DshToolAdapterOptions = {},
): PlatformClient {
  const prefix = options.callIdPrefix ?? DEFAULT_CALL_ID_PREFIX;
  const resolveToolName =
    options.resolveToolName ?? ((raw: string) => toModelToolName(raw));
  let sequence = 0;
  return {
    async callTool(rawName, params) {
      sequence += 1;
      const request: DshToolExecutionRequest = {
        // The bridge passes the raw platform tool name; DSH keys `ctx.tools` by
        // the model-visible `mcp__acp__<raw>` name.
        name: resolveToolName(rawName),
        arguments: params,
        callId: `${prefix}-${sequence}`,
        signal: options.signal ?? new AbortController().signal,
      };
      let outcome: unknown;
      try {
        outcome = await runtime.execute(request);
      } catch (error) {
        return failureResult(rawName, error instanceof Error ? error.message : error);
      }
      if (!isRecord(outcome)) {
        return failureResult(rawName, "ctx.tools.execute returned a non-object");
      }
      if (outcome["isError"] === true) {
        return failureResult(rawName, failureMessage(outcome));
      }
      const value = outcome["value"];
      const structured = isRecord(value) && isRecord(value["structuredContent"])
        ? value["structuredContent"]
        : isRecord(value)
          ? value
          : {};
      return { isError: false, structuredContent: structured };
    },
  };
}
