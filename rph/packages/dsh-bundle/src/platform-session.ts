/**
 * The public platform-session adapter (M3-01b, G02/G21).
 *
 * One place decides what a *real* platform session is, so no consumer can
 * pretend to have one:
 *
 *   1. the transport lifecycle stays outside this package (`mcp-stdio-launch.ts`
 *      is the only launcher; DSH owns `ctx.tools`), this module only consumes an
 *      injected transport/runtime;
 *   2. `initialize` (stdio) or the host's handshake (ctx.tools) must have
 *      produced a real protocol version — nothing here defaults it;
 *   3. the research extension is negotiated through the frozen application-level
 *      carrier (`tools/call: acp_capabilities`, M0-09/ADR-006 D7) and the
 *      negotiated set is taken **only** from the server's `extensions[]`
 *      answer.  There is no option, constant or fallback that can inject a
 *      negotiated extension: "not forged" is structural, not a convention;
 *   4. the handshake runs **before** `tools/list`: a server hides the ten P2
 *      tools until the session negotiates (M2-01c), so a pre-handshake list can
 *      never show the negotiated surface (F7, found by M4-01);
 *   5. `tools/list` is injected verbatim and checked for coverage, so a session
 *      never claims a tool surface it did not observe;
 *   6. product mode refuses to start on a protocol mismatch, an un-negotiated
 *      P2 extension, or a tool list that does not cover the promised surface
 *      (fail-closed, typed codes).
 *
 * `acceptance` mode exists for the M1-06/M3-01 evidence run: it still records
 * the real advertisement, but lets a P1-only session continue — the negotiated
 * set stays empty, so a P2 call is refused by the bridge exactly as before.
 */

import {
  Bridge,
  BRIDGE_EXTENSION_ID,
  BRIDGE_EXTENSION_VERSION,
  P1_TOOL_NAMES,
  P2_OPERATION_MAP,
  negotiateResearch,
  type NegotiatedExtension,
  type PlatformClient,
  type ResearchHandshake,
} from "@rph/bridge";

import type { RestorationSink } from "./platform-result.js";
import {
  MCP_PROTOCOL_VERSION,
  platformClientFromMcp,
  type McpStdioClient,
  type McpToolDescriptor,
} from "./mcp-stdio.js";

/** Session modes: product fail-fast vs evidence/acceptance. */
export type PlatformSessionMode = "product" | "acceptance";

/** The ten P2 tool names the session must observe in product mode. */
export const REQUIRED_P2_TOOL_NAMES: readonly string[] = Object.freeze([
  ...new Set(Object.values(P2_OPERATION_MAP).map((mapping) => mapping.tool)),
].sort());

/** The eight frozen P1 tool names. */
export const REQUIRED_P1_TOOL_NAMES: readonly string[] = P1_TOOL_NAMES;

/** Typed startup refusal (never a partially started session). */
export class PlatformSessionError extends Error {
  readonly code: string;
  readonly params: Readonly<Record<string, unknown>>;

  constructor(
    code: string,
    message: string,
    params: Readonly<Record<string, unknown>> = {},
  ) {
    super(`${code}: ${message}`);
    this.name = "PlatformSessionError";
    this.code = code;
    this.params = params;
  }
}

/** What a caller may configure.  Note the absence of any extension option. */
export interface PlatformSessionOptions {
  /** `product` fails closed; `acceptance` records the real answer and continues. */
  readonly mode?: PlatformSessionMode;
  /** Advisory project hint for the handshake (the server binding still decides). */
  readonly projectId?: string;
  /** Client identity recorded by the server for audit only. */
  readonly client?: { readonly name: string; readonly version?: string };
  /** Expected protocol version (defaults to the one this client speaks). */
  readonly expectedProtocolVersion?: string;
  /** Receives every F2 restoration report of this session (trace/acceptance). */
  readonly onRestore?: RestorationSink;
}

/** One real platform session: negotiated facts plus the bridge that uses them. */
export interface PlatformSession {
  readonly mode: PlatformSessionMode;
  readonly protocolVersion: string;
  readonly serverInfo: Readonly<Record<string, unknown>> | null;
  /** `tools/list` exactly as the server advertised it. */
  readonly tools: readonly McpToolDescriptor[];
  readonly toolNames: readonly string[];
  /** The real handshake answer (`status: negotiated|advertised|refused|absent`). */
  readonly handshake: ResearchHandshake;
  /** Taken only from `handshake.negotiatedExtensions`. */
  readonly negotiatedExtensions: readonly NegotiatedExtension[];
  /**
   * The verbatim platform client (the injected transport, never a second one).
   *
   * M3-02 needs it for the P2 reads the composition root builds (approval,
   * resume); the bridge stays the P1 semantic layer.
   */
  readonly client: PlatformClient;
  /** The bridge, bound to exactly those extensions. */
  readonly bridge: Bridge;
  /** True when P2 may be called (product mode requires it). */
  readonly researchEnabled: boolean;
}

function missingToolNames(
  observed: readonly string[],
  required: readonly string[],
): string[] {
  const present = new Set(observed);
  return required.filter((name) => !present.has(name));
}

/**
 * The two product-mode refusals that do **not** depend on the tool surface.
 *
 * Split out so `openMcpSession` can fail closed on a protocol mismatch or an
 * un-negotiated extension *before* it lists tools: a server that hides the P2
 * tools until negotiation would otherwise be judged on a list nobody could
 * have observed (F7).
 */
function handshakeRefusal(input: {
  readonly protocolVersion: string;
  readonly expectedProtocolVersion: string;
  readonly handshake: ResearchHandshake;
}): PlatformSessionError | null {
  if (input.protocolVersion !== input.expectedProtocolVersion) {
    return new PlatformSessionError(
      "protocol_version_mismatch",
      `the server answers ${input.protocolVersion || "<none>"}, this client speaks ${input.expectedProtocolVersion}`,
      { expected: input.expectedProtocolVersion, observed: input.protocolVersion || null },
    );
  }
  if (input.handshake.status !== "negotiated") {
    return new PlatformSessionError(
      "research_extension_not_negotiated",
      `the platform did not negotiate ${BRIDGE_EXTENSION_ID}@${BRIDGE_EXTENSION_VERSION}`,
      {
        extension: BRIDGE_EXTENSION_ID,
        expected_version: BRIDGE_EXTENSION_VERSION,
        status: input.handshake.status,
        refusal: input.handshake.refusal,
      },
    );
  }
  return null;
}

/**
 * Build a session from already observed facts.
 *
 * Every argument is evidence: a protocol version the transport answered, a tool
 * list the server advertised, a handshake the server produced.  Product mode
 * refuses when that evidence is incomplete — it never fills a gap with a
 * default.
 */
export function buildPlatformSession(input: {
  readonly mode?: PlatformSessionMode;
  readonly protocolVersion: string;
  readonly serverInfo?: Readonly<Record<string, unknown>> | null;
  readonly tools: readonly McpToolDescriptor[];
  readonly handshake: ResearchHandshake;
  readonly client: PlatformClient;
  readonly expectedProtocolVersion?: string;
}): PlatformSession {
  const mode: PlatformSessionMode = input.mode ?? "product";
  const expected = input.expectedProtocolVersion ?? MCP_PROTOCOL_VERSION;
  if (mode === "product") {
    const refusal = handshakeRefusal({
      protocolVersion: input.protocolVersion,
      expectedProtocolVersion: expected,
      handshake: input.handshake,
    });
    if (refusal !== null) {
      throw refusal;
    }
    const toolNames = input.tools.map((tool) => tool.name);
    const missing = missingToolNames(toolNames, [
      ...REQUIRED_P1_TOOL_NAMES,
      ...REQUIRED_P2_TOOL_NAMES,
    ]);
    if (missing.length > 0) {
      throw new PlatformSessionError(
        "tools_list_incomplete",
        `tools/list does not cover the negotiated surface: ${missing.join(", ")}`,
        { missing, observed: toolNames },
      );
    }
  }
  const negotiatedExtensions = Object.freeze([
    ...input.handshake.negotiatedExtensions,
  ]);
  const toolNames = Object.freeze(input.tools.map((tool) => tool.name));
  return Object.freeze({
    mode,
    protocolVersion: input.protocolVersion,
    serverInfo: input.serverInfo ?? null,
    tools: Object.freeze([...input.tools]),
    toolNames,
    handshake: input.handshake,
    negotiatedExtensions,
    client: input.client,
    bridge: new Bridge(input.client, { negotiatedExtensions }),
    researchEnabled: negotiatedExtensions.length > 0,
  });
}

/**
 * Open a session over an **initialized** MCP stdio client.
 *
 * The client must already have completed `initialize` (the launcher's caller
 * owns that lifecycle).  Both `tools/list` and the handshake are real calls;
 * neither is derived from configuration.
 *
 * Order is part of the contract (F7, found by M4-01): the application handshake
 * runs **first**, then `tools/list`.  A server hides the ten P2 tools until the
 * session negotiates, so listing first would judge a product session on the
 * pre-negotiation P1 surface — a session that can never pass on a correct
 * server.  A product-mode refusal that does not need the tool list (protocol
 * mismatch, un-negotiated extension) is raised before the list is requested.
 */
export async function openMcpSession(
  client: McpStdioClient,
  options: PlatformSessionOptions = {},
): Promise<PlatformSession> {
  if (!client.isInitialized) {
    throw new PlatformSessionError(
      "not_initialized",
      "openMcpSession requires a client that already completed initialize",
    );
  }
  const platform = platformClientFromMcp(
    client,
    options.onRestore === undefined ? {} : { onRestore: options.onRestore },
  );
  const mode: PlatformSessionMode = options.mode ?? "product";
  const handshake = await negotiateResearch(platform, {
    ...(options.projectId === undefined ? {} : { projectId: options.projectId }),
    ...(options.client === undefined ? {} : { client: options.client }),
  });
  if (mode === "product") {
    const refusal = handshakeRefusal({
      protocolVersion: client.protocolVersion ?? "",
      expectedProtocolVersion: options.expectedProtocolVersion ?? MCP_PROTOCOL_VERSION,
      handshake,
    });
    if (refusal !== null) {
      throw refusal;
    }
  }
  const tools = await client.listTools();
  return buildPlatformSession({
    mode,
    protocolVersion: client.protocolVersion ?? "",
    tools,
    handshake,
    client: platform,
    ...(options.expectedProtocolVersion === undefined
      ? {}
      : { expectedProtocolVersion: options.expectedProtocolVersion }),
  });
}

/**
 * Open a session over an already **handshaken DSH `ctx.tools` runtime**.
 *
 * DSH owns `initialize` and `tools/list`; the host passes the observed version
 * and tool descriptors (`observedProtocolVersion`/`tools`) so this adapter can
 * apply the same product-mode rules without pretending to have made those
 * calls.
 *
 * The host's list is the only surface this adapter can see, so it must be the
 * one observed **after** the host's own handshake when a P2 surface is claimed:
 * the platform hides the ten research tools until negotiation (F7), and product
 * mode refuses an incomplete surface instead of downgrading it silently.
 */
export async function openToolRuntimeSession(input: {
  readonly runtime: { execute(request: never): Promise<unknown> };
  readonly observedProtocolVersion: string;
  readonly tools: readonly McpToolDescriptor[];
  readonly client: PlatformClient;
  readonly options?: PlatformSessionOptions;
}): Promise<PlatformSession> {
  const options = input.options ?? {};
  const handshake = await negotiateResearch(input.client, {
    ...(options.projectId === undefined ? {} : { projectId: options.projectId }),
    ...(options.client === undefined ? {} : { client: options.client }),
  });
  return buildPlatformSession({
    ...(options.mode === undefined ? {} : { mode: options.mode }),
    protocolVersion: input.observedProtocolVersion,
    tools: input.tools,
    handshake,
    client: input.client,
    ...(options.expectedProtocolVersion === undefined
      ? {}
      : { expectedProtocolVersion: options.expectedProtocolVersion }),
  });
}
