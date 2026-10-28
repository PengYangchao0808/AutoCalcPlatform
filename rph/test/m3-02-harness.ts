/**
 * M3-02 test harness: a fake MCP stdio server and a capture console.
 *
 * This is a non-test module so `test/tsconfig.json` keeps a stable input; it
 * holds no assertion of its own.
 *
 * The fake server speaks the real framing (`initialize` → `tools/list` →
 * `tools/call`) so `openMcpSession` and the composition root are exercised for
 * real; it is an in-process `McpTransport`, never a child process.
 */

import {
  BRIDGE_EXTENSION_ID,
  BRIDGE_EXTENSION_VERSION,
  P1_TOOL_NAMES,
  P2_OPERATION_MAP,
  RESEARCH_HANDSHAKE_TOOL,
  type EvidenceView,
  type ResearchTaskRef,
} from "@rph/bridge";
import { MCP_PROTOCOL_VERSION, type McpTransport } from "@rph/dsh-bundle";
import type { CliIo } from "@rph/cli";

/** The ten P2 tool names the session expects in product mode. */
export const ALL_P2_TOOL_NAMES: readonly string[] = [
  ...new Set(Object.values(P2_OPERATION_MAP).map((mapping) => mapping.tool)),
].sort();

export interface FakeServerOptions {
  readonly negotiated: boolean;
  readonly tools?: readonly string[];
  readonly protocolVersion?: string;
  /** Answer `tools/call` for P2 operations with this payload. */
  readonly approvalStatus?: string;
  /** The task this server "creates" on `acp_submit` (ADR-003 identity). */
  readonly taskId?: string;
  readonly attempt?: number;
  readonly releaseId?: string;
}

/** The one ADR-003 task reference a real submit answer carries. */
export const FAKE_RELEASE_DIGEST = "sha256:" + "e".repeat(64);

export function fakeTaskRefPayload(
  projectId: string,
  options: FakeServerOptions = { negotiated: true },
): Readonly<Record<string, unknown>> {
  const taskId = options.taskId ?? "20261027_001_Optimize";
  return {
    schema: "acp2.runtime.task-ref.v1",
    project_id: projectId,
    task_id: taskId,
    job_id: taskId,
    attempt: options.attempt ?? 1,
    release: {
      release_id: options.releaseId ?? "release-m3-02",
      release_digest: FAKE_RELEASE_DIGEST,
    },
  };
}

type Frame = Record<string, unknown>;

function isFrame(value: unknown): value is Frame {
  return typeof value === "object" && value !== null;
}

function capabilitiesEnvelope(negotiated: boolean): Readonly<Record<string, unknown>> {
  return {
    schema_version: 1,
    operation: RESEARCH_HANDSHAKE_TOOL,
    status: "ok",
    data: {
      protocol_version: MCP_PROTOCOL_VERSION,
      tools: [...P1_TOOL_NAMES],
      extensions: [
        {
          id: BRIDGE_EXTENSION_ID,
          version: BRIDGE_EXTENSION_VERSION,
          supported: true,
          supported_versions: [BRIDGE_EXTENSION_VERSION],
          requested: { id: BRIDGE_EXTENSION_ID, version: BRIDGE_EXTENSION_VERSION },
          negotiated: negotiated,
          implemented: negotiated,
          negotiated_by: negotiated ? "tools/call" : null,
          refusal: null,
        },
      ],
    },
  };
}

/** One in-process MCP server; `sent` keeps every client frame for assertions. */
export function fakeServer(options: FakeServerOptions): {
  transport: McpTransport;
  sent: readonly Frame[];
} {
  const sent: Frame[] = [];
  let onLine: ((line: string) => void) | null = null;
  const reply = (frame: Frame, result: Frame): void => {
    queueMicrotask(() =>
      onLine?.(
        JSON.stringify({ jsonrpc: "2.0", id: frame["id"], result }),
      ),
    );
  };
  const transport: McpTransport = {
    send(raw: string) {
      const frame = JSON.parse(raw) as Frame;
      sent.push(frame);
      if (typeof frame["id"] !== "number") return;
      if (frame["method"] === "initialize") {
        reply(frame, {
          protocolVersion: options.protocolVersion ?? MCP_PROTOCOL_VERSION,
          capabilities: { tools: {} },
          serverInfo: { name: "acp", version: "0.3.0" },
        });
        return;
      }
      if (frame["method"] === "tools/list") {
        const tools = options.tools ?? [...P1_TOOL_NAMES, ...ALL_P2_TOOL_NAMES];
        reply(frame, {
          tools: tools.map((name) => ({ name, description: name, inputSchema: { type: "object" } })),
        });
        return;
      }
      if (frame["method"] === "tools/call") {
        const params = frame["params"];
        const name = isFrame(params) ? params["name"] : undefined;
        if (name === RESEARCH_HANDSHAKE_TOOL) {
          const envelope = capabilitiesEnvelope(options.negotiated);
          reply(frame, {
            isError: false,
            structuredContent: envelope,
            content: [{ type: "text", text: JSON.stringify(envelope) }],
          });
          return;
        }
        const submitParams = isFrame(params) ? params["arguments"] : undefined;
        const projectId =
          isFrame(submitParams) && typeof submitParams["project_id"] === "string"
            ? submitParams["project_id"]
            : "proj-1";
        const taskRef =
          name === "acp_submit" || name === "acp_status"
            ? fakeTaskRefPayload(projectId, options)
            : { task_id: "job-1", attempt: 1 };
        const data: Frame = {
          approvals: [
            {
              approval_ref: "ap_demo_fixture",
              status: options.approvalStatus ?? "approved",
              plan_digest: options.approvalStatus === undefined ? undefined : null,
            },
          ],
          resumed_from: "saga",
          task_ref: taskRef,
          saga: { state: "executing", terminal: false },
        };
        reply(frame, {
          isError: false,
          structuredContent: { schema_version: 1, status: "ok", data },
          content: [{ type: "text", text: JSON.stringify(data) }],
        });
        return;
      }
      reply(frame, {});
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

export interface CaptureIo extends CliIo {
  readonly out: string[];
  readonly err: string[];
}

/** Capture console with an explicit environment (never the ambient one). */
export function captureIo(env: Readonly<Record<string, string | undefined>> = {}): CaptureIo {
  const out: string[] = [];
  const err: string[] = [];
  return {
    out,
    err,
    env,
    stdout: (line: string) => {
      out.push(line);
    },
    stderr: (line: string) => {
      err.push(line);
    },
  };
}

/** The last JSON document printed on stdout. */
export function lastJson(io: CaptureIo): Record<string, unknown> {
  const text = io.out.join("\n");
  const start = text.indexOf("{");
  if (start < 0) {
    throw new Error(`no JSON on stdout: ${text}`);
  }
  return JSON.parse(text.slice(start)) as Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// Evidence projection fixture (M3-04 binding mode)
// ---------------------------------------------------------------------------

export const FAKE_REPORT_ID = "report-m3-02";
export const FAKE_REPORT_DIGEST = "sha256:" + "c".repeat(64);
export const FAKE_PROJECTION_DIGEST = "sha256:" + "d".repeat(64);

/**
 * The single `acp2.runtime.evidence-projection.v1` shape the Supervisor binds.
 *
 * It is a fixture fed through the injected evidence reader; the Supervisor still
 * attributes it and derives the decision itself.
 */
export function fakeEvidenceView(
  ref: ResearchTaskRef,
  researchId: string,
  round: number,
): EvidenceView {
  return {
    schema: "acp2.runtime.evidence-projection.v1",
    contract_version: "1.0.0",
    research: {
      principal_id: "principal-m3-02",
      project_id: ref.project_id,
      research_id: researchId,
      session_id: "session-m3-02",
      round,
    },
    task_ref: ref,
    report: {
      report_id: FAKE_REPORT_ID,
      digest: FAKE_REPORT_DIGEST,
      schema_version: 1,
      subject_id: ref.task_id,
    },
    execution_terminal_state: "completed",
    qualification: "qualified",
    evidence_status: "complete",
    claims: [
      {
        claim_id: "claim-m3-02",
        status: "supported",
        validator_id: "cccp.analysis.research.energy",
        validator_version: "1.0.0",
        source_ref_count: 1,
        blocking_missing_checks: [],
      },
    ],
    overall_claim_status: "supported",
    blocking_missing_checks: [],
    evidence_refs: [
      {
        evidence_id: "ev-m3-02",
        kind: "task_result",
        sha256: "sha256:" + "a".repeat(64),
        relative_path: "WORK/task_result.json",
      },
    ],
    limitations: [],
    provenance_digest: "sha256:" + "a".repeat(64),
    normal_reuse: true,
    projection_digest: FAKE_PROJECTION_DIGEST,
  };
}
