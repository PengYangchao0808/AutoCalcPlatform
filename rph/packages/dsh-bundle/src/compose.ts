/**
 * The dsh-bundle composition root (M3-02a).
 *
 * `@rph/cli` imports **only** this package, so every wiring decision lives here:
 *
 *   real session (`platform-session.ts`) + bridge (P1) + research ports (P2)
 *     -> Supervisor skills (M3-03 model adapter, or an explicit fixture)
 *     -> Supervisor ports (approval/persist from `research-ports.ts`)
 *
 * `composeResearchRuntime` is fail-fast: a product/acceptance run that lacks a
 * model planner, the model gateway, the platform session (P1+P2 surface), a
 * durable research writer or the artifact/evidence path returns **every**
 * missing requirement at once in one typed error. Nothing is filled with a
 * fixture and no Supervisor state machine is copied here.
 */

import {
  REQUIRED_P1_TOOL_NAMES,
  REQUIRED_P2_TOOL_NAMES,
  PlatformSessionError,
  openMcpSession,
  type PlatformSession,
  type PlatformSessionMode,
} from "./platform-session.js";
import {
  MCP_DEFAULT_TIMEOUT_MS,
  McpStdioClient,
  type McpTransport,
} from "./mcp-stdio.js";
import {
  acpMcpServeArgs,
  launchMcpStdio,
  type LaunchedMcp,
} from "./mcp-stdio-launch.js";
import { createDemoPorts, DEMO_EVIDENCE_KIND } from "./demo.js";
import {
  FIXTURE_WRITER_KINDS,
  PlatformPortError,
  createPlatformPorts,
  type BudgetUsageReaderPort,
  type EvidenceReaderPort,
  type PlatformPortAdapters,
  type ResearchWriterPort,
  type SubmitPayloadMapper,
} from "./research-ports.js";
import type {
  ModelGatewayUsage,
  SubmitIntentPort,
  SupervisorPorts,
  SupervisorSkills,
} from "../skills/supervisor.js";
import type { RuntimeEnvironment, ResolvedRuntimeProfile } from "./runtime-profile.js";
import { BUNDLE_VERSION } from "./version.js";

/** Three explicit composition modes. `demo` is a labelled fixture. */
export const COMPOSITION_MODES = Object.freeze([
  "product",
  "acceptance",
  "demo",
] as const);

export type CompositionMode = (typeof COMPOSITION_MODES)[number];

export function isCompositionMode(value: string): value is CompositionMode {
  return (COMPOSITION_MODES as readonly string[]).includes(value);
}

/** One product prerequisite (ordered: the report lists them in this order). */
export const READINESS_REQUIREMENTS = Object.freeze([
  "model_skills",
  "model_gateway",
  "platform_session",
  "p1_tool_surface",
  "p2_tool_surface",
  "research_writer",
  "artifact_evidence_path",
  "science_seed",
  "profile_parameters",
  "evidence_binding",
  "budget_usage",
] as const);

export type ReadinessRequirement = (typeof READINESS_REQUIREMENTS)[number];

/** One typed missing prerequisite. */
export interface ReadinessIssue {
  readonly requirement: ReadinessRequirement;
  readonly code: string;
  readonly detail: string;
  readonly hint: string;
}

/** Fail-fast error carrying every missing prerequisite (never just the first). */
export class ProductCompositionError extends Error {
  readonly mode: CompositionMode;
  readonly issues: readonly ReadinessIssue[];

  constructor(mode: CompositionMode, issues: readonly ReadinessIssue[]) {
    super(
      `${mode} composition is incomplete: ${issues
        .map((issue) => `${issue.requirement}(${issue.code})`)
        .join(", ")}`,
    );
    this.name = "ProductCompositionError";
    this.mode = mode;
    this.issues = Object.freeze([...issues]);
  }
}

export interface ReadinessInput {
  readonly mode: CompositionMode;
  readonly environment: RuntimeEnvironment;
  readonly skills?: SupervisorSkills | null;
  readonly modelGateway?: (() => Promise<ModelGatewayUsage>) | null;
  readonly session?: PlatformSession | null;
  /** Failure captured while opening the session (typed, never thrown past here). */
  readonly sessionError?: { readonly code: string; readonly message: string } | null;
  readonly writer?: ResearchWriterPort | null;
  readonly artifactEvidencePath?: string | null;
  /** The typed science seed (charge/spin/method/provenance) is mandatory. */
  readonly scienceSeedPresent?: boolean;
  /** Requirement policy of the environment (`RUNTIME_PROFILE_MATRIX`). */
  readonly profile: ResolvedRuntimeProfile;
  /** The single evidence-projection reader (product binding mode needs it). */
  readonly evidenceReader?: EvidenceReaderPort | null;
  /** Platform saga/receipt budget counters (product resume needs them). */
  readonly budgetUsage?: BudgetUsageReaderPort | null;
}

function issue(
  requirement: ReadinessRequirement,
  code: string,
  detail: string,
  hint: string,
): ReadinessIssue {
  return Object.freeze({ requirement, code, detail, hint });
}

function missingToolNames(observed: readonly string[], required: readonly string[]): string[] {
  const present = new Set(observed);
  return required.filter((name) => !present.has(name));
}

/**
 * Collect **all** missing product prerequisites, in one list.
 *
 * `demo` has no prerequisites (the CLI still requires an explicit `--mode demo`
 * and refuses a real Controller). Every other mode checks the whole matrix so a
 * caller sees one complete list and a non-zero exit, not a one-at-a-time loop.
 */
export function collectReadinessIssues(input: ReadinessInput): readonly ReadinessIssue[] {
  if (input.mode === "demo") {
    return Object.freeze([]);
  }
  const issues: ReadinessIssue[] = [];
  const profile = input.profile.spec;

  if (input.skills === null || input.skills === undefined) {
    issues.push(
      issue(
        "model_skills",
        "model_skills_missing",
        "no supervisor skills were composed",
        "wire the model planner through the Controller model gateway (M3-03)",
      ),
    );
  } else if (input.skills.planner_kind !== "model") {
    issues.push(
      issue(
        "model_skills",
        "model_skills_fixture",
        `the composed planner kind is ${String(input.skills.planner_kind)}; a product run requires the model adapter`,
        "use --mode demo for the labelled fixture, or compose the model planner",
      ),
    );
  }

  if (
    profile.require_model_gateway &&
    (input.modelGateway === null || input.modelGateway === undefined)
  ) {
    issues.push(
      issue(
        "model_gateway",
        "model_gateway_missing",
        "the platform model gateway port is absent",
        "inject the Controller model gateway (M3-03); the Supervisor never settles usage locally",
      ),
    );
  }

  if (input.session === null || input.session === undefined) {
    issues.push(
      issue(
        "platform_session",
        input.sessionError === null || input.sessionError === undefined
          ? "platform_session_missing"
          : `platform_session_unavailable:${input.sessionError.code}`,
        input.sessionError === null || input.sessionError === undefined
          ? "no real platform session was opened"
          : `opening the platform session failed: ${input.sessionError.message}`,
        "set ACP2_CONTROLLER_URL and RPH_INTERPRETER, or use --mode demo for a labelled fixture",
      ),
    );
  } else {
    const missingP1 = missingToolNames(input.session.toolNames, REQUIRED_P1_TOOL_NAMES);
    if (missingP1.length > 0) {
      issues.push(
        issue(
          "p1_tool_surface",
          "p1_tool_surface_incomplete",
          `tools/list is missing ${missingP1.join(", ")}`,
          "the Controller must expose the eight frozen P1 tools",
        ),
      );
    }
    if (input.profile.spec.fail_closed_tool_surface) {
      const missingP2 = missingToolNames(input.session.toolNames, REQUIRED_P2_TOOL_NAMES);
      if (missingP2.length > 0) {
        issues.push(
          issue(
            "p2_tool_surface",
            "p2_tool_surface_incomplete",
            `tools/list is missing ${missingP2.join(", ")}`,
            "enable the acp.research.v1 extension on the Controller",
          ),
        );
      }
    }
  }

  if (input.writer === null || input.writer === undefined) {
    issues.push(
      issue(
        "research_writer",
        "research_writer_missing",
        "no research writer is injected; a product run cannot persist records or evidence",
        "provide the store-backed ResearchWriter (M3-04)",
      ),
    );
  } else if (
    profile.require_research_writer &&
    (FIXTURE_WRITER_KINDS.includes(input.writer.kind) || input.writer.kind.includes("fixture"))
  ) {
    issues.push(
      issue(
        "research_writer",
        "research_writer_fixture",
        `the injected writer is the ${input.writer.kind} fixture`,
        "inject the durable writer; the in-memory writer is demo/test only",
      ),
    );
  }

  if (input.artifactEvidencePath === null || input.artifactEvidencePath === undefined) {
    issues.push(
      issue(
        "artifact_evidence_path",
        "artifact_evidence_path_missing",
        "the artifact/evidence path of this deployment is unknown",
        "set RPH_ARTIFACT_EVIDENCE_PATH",
      ),
    );
  }

  if (input.profile.issues.length > 0) {
    issues.push(
      issue(
        "profile_parameters",
        "parameters_missing",
        `the ${input.environment} profile is missing ${input.profile.issues
          .map((entry) => `${entry.setting}(${entry.env})`)
          .join(", ")}`,
        "supply every required parameter from the environment, or use dev for a local run",
      ),
    );
  }

  if (input.scienceSeedPresent === false) {
    issues.push(
      issue(
        "science_seed",
        "science_seed_missing",
        "no typed science seed (charge/multiplicity/method/provenance) was supplied",
        "pass --science <file>; the product planner never defaults charge or multiplicity",
      ),
    );
  }

  // Product mode runs `require_evidence_binding: true` (M3-04): a verified
  // `EvidenceView`, a CAS decision and a fenced checkpoint are mandatory, and a
  // resumed run restores its budget from platform receipts. Those facts come
  // from injected readers/writers; absence is a typed fail-fast, never a
  // downgraded verdict.
  if (input.mode === "product") {
    if (input.evidenceReader === null || input.evidenceReader === undefined) {
      issues.push(
        issue(
          "evidence_binding",
          "evidence_reader_missing",
          "no reader for the single evidence projection is injected",
          "inject EvidenceReaderPort (the research-record evidence route adapter)",
        ),
      );
    }
    if (input.writer !== null && input.writer !== undefined) {
      if (input.writer.persistDecision === undefined) {
        issues.push(
          issue(
            "evidence_binding",
            "decision_writer_missing",
            "the research writer has no decision CAS",
            "implement persistDecision on the durable writer",
          ),
        );
      }
      if (input.writer.persistCheckpoint === undefined) {
        issues.push(
          issue(
            "evidence_binding",
            "checkpoint_writer_missing",
            "the research writer has no fenced checkpoint",
            "implement persistCheckpoint on the durable writer",
          ),
        );
      }
    }
    if (input.budgetUsage === null || input.budgetUsage === undefined) {
      issues.push(
        issue(
          "budget_usage",
          "budget_usage_source_missing",
          "no platform budget/receipt source is injected",
          "inject BudgetUsageReaderPort (saga record/receipt counters); a resume never re-estimates usage",
        ),
      );
    }
  }

  return Object.freeze(issues);
}

/** Throw one `ProductCompositionError` listing every missing prerequisite. */
export function assertProductReady(input: ReadinessInput): void {
  const issues = collectReadinessIssues(input);
  if (issues.length > 0) {
    throw new ProductCompositionError(input.mode, issues);
  }
}

export interface ComposeRuntimeInput extends ReadinessInput {
  readonly adapters?: PlatformPortAdapters;
  /** The proposal whose saga a resume advances (P2-R5 identity). */
  readonly proposalRef?: string | null;
  /** Enables the real bridge-backed submit port (plan → platform payload). */
  readonly submitPayload?: SubmitPayloadMapper;
  /** Override the M1-02 intent ledger (default: the session bridge's). */
  readonly submitIntent?: SubmitIntentPort;
}

export interface ComposedResearchRuntime {
  readonly mode: CompositionMode;
  readonly environment: RuntimeEnvironment;
  readonly skills: SupervisorSkills;
  readonly ports: SupervisorPorts;
  readonly session: PlatformSession | null;
  readonly writer: ResearchWriterPort | null;
  /** `fixture` for the labelled demo, `live` for a platform-backed run. */
  readonly evidence_kind: "fixture" | "live";
}

/**
 * Compose one runnable research runtime.
 *
 * @throws ProductCompositionError listing every missing prerequisite.
 */
export function composeResearchRuntime(input: ComposeRuntimeInput): ComposedResearchRuntime {
  assertProductReady(input);
  if (input.skills === null || input.skills === undefined) {
    throw new PlatformPortError(
      "demo_skills_missing",
      "an explicit skills binding is required (test skills in demo mode)",
    );
  }
  if (input.mode === "demo") {
    return Object.freeze({
      mode: input.mode,
      environment: input.environment,
      skills: input.skills,
      ports: createDemoPorts({
        project_id: "demo-project",
        research_id: "demo-research",
        question: "demo question",
      }),
      session: null,
      writer: input.writer ?? null,
      evidence_kind: DEMO_EVIDENCE_KIND,
    });
  }
  const session = input.session;
  if (session === null || session === undefined) {
    // `assertProductReady` already reported it; this keeps the type honest.
    throw new PlatformPortError("platform_session_missing", "no platform session");
  }
  const writer = input.writer;
  if (writer === null || writer === undefined) {
    throw new PlatformPortError("research_writer_missing", "no research writer");
  }
  const adapters: PlatformPortAdapters = {
    ...(input.adapters ?? {}),
    ...(input.modelGateway === null || input.modelGateway === undefined
      ? {}
      : { modelGateway: input.modelGateway }),
  };
  const evidenceReader = input.evidenceReader;
  const budgetUsage = input.budgetUsage;
  return Object.freeze({
    mode: input.mode,
    environment: input.environment,
    skills: input.skills,
    ports: createPlatformPorts({
      session,
      writer,
      adapters,
      ...(evidenceReader === null || evidenceReader === undefined ? {} : { evidenceReader }),
      ...(budgetUsage === null || budgetUsage === undefined ? {} : { budgetUsage }),
      ...(input.proposalRef === null || input.proposalRef === undefined
        ? {}
        : { proposalRef: input.proposalRef }),
      ...(input.submitPayload === undefined ? {} : { submitPayload: input.submitPayload }),
      ...(input.submitIntent === undefined ? {} : { submitIntent: input.submitIntent }),
    }),
    session,
    writer,
    evidence_kind: "live" as const,
  });
}

// ---------------------------------------------------------------------------
// Platform connect: the only place a client process may be started
// ---------------------------------------------------------------------------

/** Facts the doctor reports after a successful connect. */
export interface ConnectedPlatform {
  readonly session: PlatformSession;
  readonly protocolVersion: string;
  readonly serverInfo: Readonly<Record<string, unknown>> | null;
  readonly toolNames: readonly string[];
  readonly extensionStatus: string;
  close(): Promise<void>;
}

export interface ConnectPlatformInput {
  readonly profile: ResolvedRuntimeProfile;
  readonly mode?: PlatformSessionMode;
  readonly projectId?: string;
  readonly client?: { readonly name: string; readonly version?: string };
  readonly timeoutMs?: number;
  /**
   * Test/DSH seam: an already created transport (no child process).
   *
   * When absent the named launcher starts `acp mcp serve` — the one approved
   * process seam of this workspace.
   */
  readonly createTransport?: () => McpTransport;
}

/**
 * Open one real platform session through the named launcher.
 *
 * The launcher owns the child; this function owns the handshake and the
 * product/acceptance session rules. A missing profile parameter is a typed
 * `PlatformPortError`, never a fallback to a developer machine.
 */
export async function connectPlatform(input: ConnectPlatformInput): Promise<ConnectedPlatform> {
  const controllerUrl = input.profile.get("controller_url");
  const interpreter = input.profile.get("interpreter");
  if (controllerUrl === null) {
    throw new PlatformPortError(
      "profile_incomplete",
      "controller_url is required to connect (set ACP2_CONTROLLER_URL)",
      { setting: "controller_url" },
    );
  }
  if (interpreter === null) {
    throw new PlatformPortError(
      "profile_incomplete",
      "interpreter is required to connect (set RPH_INTERPRETER)",
      { setting: "interpreter" },
    );
  }

  let launched: LaunchedMcp | null = null;
  let client: McpStdioClient;
  if (input.createTransport === undefined) {
    const env: Record<string, string> = {};
    const runRoot = input.profile.get("run_root");
    const configPath = input.profile.get("config_path");
    const workingDirectory = input.profile.get("working_directory");
    if (runRoot !== null) env["ACP_RUN_ROOT"] = runRoot;
    if (configPath !== null) env["ACP_CONFIG"] = configPath;
    env["ACP_CONTROLLER_URL"] = controllerUrl;
    launched = launchMcpStdio({
      command: interpreter,
      args: acpMcpServeArgs(controllerUrl),
      ...(workingDirectory === null ? {} : { cwd: workingDirectory }),
      env,
      timeoutMs: input.timeoutMs ?? MCP_DEFAULT_TIMEOUT_MS,
    });
    client = launched.client;
  } else {
    client = new McpStdioClient(input.createTransport(), {
      ...(input.timeoutMs === undefined ? {} : { timeoutMs: input.timeoutMs }),
    });
  }

  const close = async (): Promise<void> => {
    if (launched !== null) {
      await launched.close();
      return;
    }
    client.close();
  };

  try {
    await client.initialize();
    const session = await openMcpSession(client, {
      ...(input.mode === undefined ? {} : { mode: input.mode }),
      ...(input.projectId === undefined ? {} : { projectId: input.projectId }),
      client: {
        name: input.client?.name ?? "rph-doctor",
        version: input.client?.version ?? BUNDLE_VERSION,
      },
    });
    return Object.freeze({
      session,
      protocolVersion: session.protocolVersion,
      serverInfo: session.serverInfo,
      toolNames: session.toolNames,
      extensionStatus: session.handshake.status,
      close,
    });
  } catch (error) {
    await close();
    if (error instanceof PlatformSessionError) {
      throw new PlatformPortError(error.code, error.message, error.params);
    }
    throw new PlatformPortError(
      "platform_connect_failed",
      error instanceof Error ? error.message : String(error),
    );
  }
}
