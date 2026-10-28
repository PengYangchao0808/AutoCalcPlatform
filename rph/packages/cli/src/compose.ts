/**
 * The CLI composition root (M3-02a).
 *
 * Everything the CLI starts comes from this one module: the resolved profile,
 * the (optional) real platform session, the injected product parts, the
 * ready/incomplete decision and the Supervisor invocation. The CLI imports
 * `@rph/dsh-bundle` only; `composeResearchRuntime` in the bundle is the single
 * place that builds the ports, and the Supervisor state machine is imported, not
 * copied.
 *
 * Exit codes:
 *   0  the command ran (a Supervisor stop is a research outcome, not an error)
 *   1  the command failed (typed platform/port error)
 *   2  usage error
 *   3  composition is incomplete — every missing prerequisite is printed
 */

import { readFileSync } from "node:fs";

import {
  FIXTURE_SUPERVISOR_SKILLS,
  PlatformPortError,
  ProductCompositionError,
  assertProductionSkills,
  collectReadinessIssues,
  composeResearchRuntime,
  connectPlatform,
  createMemoryResearchWriter,
  resolveRuntimeProfile,
  resumeResearch,
  runDoctor,
  runSupervisor,
  validateScienceSeed,
  type ComposeRuntimeInput,
  type CompositionMode,
  type ConnectedPlatform,
  type DoctorReadinessFacts,
  type DoctorReport,
  type BudgetUsageReaderPort,
  type EvidenceReaderPort,
  type McpTransport,
  type ModelGatewayUsage,
  type PlatformPortAdapters,
  type PlatformSession,
  type ReadinessIssue,
  type ResearchWriterPort,
  type ResolvedRuntimeProfile,
  type RuntimeEnvironment,
  type ScienceSeed,
  type SubmitIntentPort,
  type SubmitPayloadMapper,
  type SupervisorInput,
  type SupervisorResult,
  type SupervisorSkills,
} from "@rph/dsh-bundle";

import {
  CLI_VERSION,
  CliUsageError,
  parseArgs,
  renderInformation,
  type CliOptions,
} from "./index.js";

export const EXIT_OK = 0;
export const EXIT_FAILED = 1;
export const EXIT_USAGE = 2;
export const EXIT_INCOMPLETE = 3;

/** Console seam (tests capture it; nothing here touches the process). */
export interface CliIo {
  readonly stdout: (line: string) => void;
  readonly stderr: (line: string) => void;
  readonly env: Readonly<Record<string, string | undefined>>;
}

export const processIo: CliIo = Object.freeze({
  stdout: (line: string) => {
    process.stdout.write(`${line}\n`);
  },
  stderr: (line: string) => {
    process.stderr.write(`${line}\n`);
  },
  env: process.env,
});

/** Wiring the caller may inject (product parts, M3-03/M3-04, tests). */
export interface CliRuntimeParts {
  readonly skills?: SupervisorSkills | null;
  readonly writer?: ResearchWriterPort | null;
  readonly modelGateway?: (() => Promise<ModelGatewayUsage>) | null;
  readonly adapters?: PlatformPortAdapters;
  readonly scienceSeed?: ScienceSeed | null;
  readonly initialInput?: {
    readonly input_id: string;
    readonly sha256: string;
    readonly format: string;
    readonly role: string | null;
  } | null;
  /** The single evidence projection reader (product binding mode). */
  readonly evidenceReader?: EvidenceReaderPort | null;
  /** Platform saga/receipt budget counters (product resume). */
  readonly budgetUsage?: BudgetUsageReaderPort | null;
  /** The proposal whose saga a resume advances (P2-R5). */
  readonly proposalRef?: string | null;
  /** Plan → platform submit payload; enables the bridge-backed submit port. */
  readonly submitPayload?: SubmitPayloadMapper;
  /** Override the M1-02 intent ledger. */
  readonly submitIntent?: SubmitIntentPort;
}

export interface CliPartContext {
  readonly environment: RuntimeEnvironment;
  readonly mode: CompositionMode;
  readonly profile: ResolvedRuntimeProfile;
  readonly session: PlatformSession | null;
}

export interface CliDependencies {
  /** Product composition seam (default: nothing wired — the matrix reports it). */
  readonly parts?: (context: CliPartContext) => CliRuntimeParts | Promise<CliRuntimeParts>;
  /** `--connect` transport seam; absent = the named `acp mcp serve` launcher. */
  readonly createTransport?: () => McpTransport;
}

/** A refusal that is not a readiness requirement (demo/usage policy). */
interface Refusal {
  readonly code: string;
  readonly detail: string;
  readonly hint: string;
}

/** Demo science values: fixtures, never scientific claims. */
const DEMO_SCIENCE_SEED: ScienceSeed = Object.freeze({
  method: "r2SCAN-3c",
  mapping: "identity",
  charge: 0,
  multiplicity: 1,
  electronic_state: Object.freeze({
    charge: 0,
    multiplicity: 1,
    state_label: "singlet",
    spin_polarization: 0,
  }),
  structure_provenance: Object.freeze([
    Object.freeze({
      input_id: "demo-input-1",
      sha256: "sha256:" + "d".repeat(64),
      format: "smiles",
      role: "minimum",
    }),
  ]),
  constraints: Object.freeze([]),
});

const DEMO_INITIAL_INPUT = Object.freeze({
  input_id: "demo-input-1",
  sha256: "sha256:" + "d".repeat(64),
  format: "smiles",
  role: "minimum",
});

const DEMO_RECIPE_DIGEST = "sha256:" + "e".repeat(64);

/**
 * The client-side round/budget cap handed to the Supervisor.
 *
 * It limits this client's own loop; it is not a platform budget claim — the
 * Controller admission and the model gateway remain the authoritative limits.
 */
function clientLimits(maxRounds: number): SupervisorInput["limits"] {
  return Object.freeze({
    max_rounds: maxRounds,
    budget: Object.freeze({
      model_requests: 8,
      input_tokens: 100_000,
      output_tokens: 50_000,
      task_refs: 4,
      recipe_steps: 8,
      qc_invocations: 8,
      wall_seconds: 7_200,
      queue_seconds: 600,
    }),
  });
}

function overrideEnv(
  env: Readonly<Record<string, string | undefined>>,
  options: CliOptions,
): Readonly<Record<string, string | undefined>> {
  return options.controller === undefined
    ? env
    : Object.freeze({ ...env, ACP2_CONTROLLER_URL: options.controller });
}

function printJson(io: CliIo, value: unknown): void {
  io.stdout(JSON.stringify(value, null, 2));
}

function refusalLines(refusals: readonly Refusal[], issues: readonly ReadinessIssue[]): string[] {
  return [
    ...refusals.map((refusal) => `  - ${refusal.code}: ${refusal.detail} (${refusal.hint})`),
    ...issues.map(
      (issue) => `  - ${issue.code} [${issue.requirement}]: ${issue.detail} (${issue.hint})`,
    ),
  ];
}

interface ScienceInput {
  readonly seed: ScienceSeed | null;
  readonly initialInput: CliRuntimeParts["initialInput"];
  readonly issues: readonly Refusal[];
}

function readScienceFile(path: string): ScienceInput {
  let parsed: unknown;
  try {
    parsed = JSON.parse(readFileSync(path, "utf8")) as unknown;
  } catch (error) {
    return {
      seed: null,
      initialInput: null,
      issues: [
        {
          code: "science_file_unreadable",
          detail: `${path}: ${error instanceof Error ? error.message : String(error)}`,
          hint: "pass a JSON file with {science_seed, initial_input}",
        },
      ],
    };
  }
  if (typeof parsed !== "object" || parsed === null) {
    return {
      seed: null,
      initialInput: null,
      issues: [
        {
          code: "science_file_invalid",
          detail: `${path}: not an object`,
          hint: "pass {science_seed, initial_input}",
        },
      ],
    };
  }
  const record = parsed as Record<string, unknown>;
  const seed = record["science_seed"];
  const issues: Refusal[] = [];
  const seedIssues = validateScienceSeed(seed);
  if (seedIssues.length > 0) {
    issues.push({
      code: "science_seed_invalid",
      detail: seedIssues.map((issue) => `${issue.path}:${issue.code}`).join(","),
      hint: "charge/multiplicity/method/provenance must be explicit (never defaulted)",
    });
  }
  const initial = record["initial_input"];
  if (typeof initial !== "object" || initial === null) {
    issues.push({
      code: "initial_input_missing",
      detail: `${path}: initial_input is required`,
      hint: "initial_input = {input_id, sha256, format, role}",
    });
  }
  return {
    seed: seedIssues.length === 0 ? (seed as ScienceSeed) : null,
    initialInput:
      typeof initial === "object" && initial !== null
        ? (initial as CliRuntimeParts["initialInput"])
        : null,
    issues,
  };
}

function supervisorInputFor(
  options: CliOptions,
  mode: CompositionMode,
  parts: CliRuntimeParts,
  profileProjectId: string | null,
): SupervisorInput {
  const projectId =
    options.project ?? (mode === "demo" ? null : profileProjectId) ?? "demo-project";
  const researchId = options.research ?? "demo-research";
  if (mode === "demo") {
    return {
      project_id: projectId,
      research_id: researchId,
      question: options.question ?? "labelled demo round",
      question_ref: null,
      constraints: [],
      available_inputs: [DEMO_INITIAL_INPUT.input_id],
      recipe_digest: options.recipeDigest ?? DEMO_RECIPE_DIGEST,
      initial_input: DEMO_INITIAL_INPUT,
      science_seed: DEMO_SCIENCE_SEED,
      require_evidence_binding: false,
      limits: clientLimits(options.maxRounds ?? 1),
    };
  }
  const seed = parts.scienceSeed;
  const initialInput = parts.initialInput;
  if (seed === null || seed === undefined || initialInput === null || initialInput === undefined) {
    // Unreachable after the readiness gate; kept structural so a product run can
    // never fall back to the demo science values.
    throw new PlatformPortError(
      "science_seed_missing",
      "a product run requires the typed science seed and the initial input",
    );
  }
  return {
    project_id: projectId,
    research_id: researchId,
    question: options.question as string,
    question_ref: null,
    constraints: [],
    available_inputs: [initialInput.input_id],
    recipe_digest: options.recipeDigest as string,
    initial_input: initialInput,
    science_seed: seed,
    // Product composition (M3-04 exit): the verdict comes only from a
    // digest-bound EvidenceView, a persisted submit intent and a CAS decision.
    require_evidence_binding: mode === "product",
    limits: clientLimits(options.maxRounds ?? 3),
  };
}

function resultSummary(result: SupervisorResult): Readonly<Record<string, unknown>> {
  return Object.freeze({
    status: result.status,
    stop_reason: result.stop_reason,
    rounds_completed: result.rounds_completed,
    submits: result.submits,
    task_refs: result.task_refs,
    model_requests_used: result.model_requests_used,
    reports: result.reports.length,
    // M3-04: the verdict is the persisted decision of a verified binding, never
    // a legacy report array length.
    bindings: result.bindings.length,
    decisions: result.decisions.length,
    decision: result.decision === null ? null : result.decision.decision,
    resume_state: result.resume_handle === null ? null : result.resume_handle.state,
    phases: result.phases.length,
  });
}

async function defaultParts(
  context: CliPartContext,
  dependencies: CliDependencies,
): Promise<CliRuntimeParts> {
  if (context.mode === "demo") {
    return {
      skills: FIXTURE_SUPERVISOR_SKILLS,
      writer: createMemoryResearchWriter(),
      scienceSeed: DEMO_SCIENCE_SEED,
      initialInput: DEMO_INITIAL_INPUT,
    };
  }
  const parts = await dependencies.parts?.(context);
  return parts ?? {};
}

/**
 * Default environment per composition mode.
 *
 * `product`/`acceptance` default to the strict `acceptance` environment so a
 * product run is never silently evaluated under dev parameters; `demo` defaults
 * to `dev` (the only environment that allows it). `--environment` always wins.
 */
function environmentFor(options: CliOptions): RuntimeEnvironment {
  if (options.environment !== undefined) {
    return options.environment;
  }
  return (options.mode ?? "product") === "demo" ? "dev" : "acceptance";
}

async function runResearch(
  options: CliOptions,
  io: CliIo,
  dependencies: CliDependencies,
): Promise<number> {
  const environment = environmentFor(options);
  const env = overrideEnv(io.env, options);
  const profile = resolveRuntimeProfile(environment, env);
  const mode: CompositionMode = options.mode ?? "product";
  const refusals: Refusal[] = [];

  if (mode === "demo") {
    if (!profile.spec.allow_demo) {
      refusals.push({
        code: "demo_not_allowed",
        detail: `the ${environment} environment forbids the demo`,
        hint: "use --environment dev, or run a product composition",
      });
    }
    if (profile.get("controller_url") !== null) {
      refusals.push({
        code: "demo_refuses_controller",
        detail: "a demo never runs against a real Controller",
        hint: "unset ACP2_CONTROLLER_URL/--controller or drop --mode demo",
      });
    }
    if (options.skills === "model") {
      refusals.push({
        code: "demo_requires_test_skills",
        detail: "a demo is a labelled fixture and must use --skills test",
        hint: "pass --skills test",
      });
    }
  } else if (options.skills === "test") {
    refusals.push({
      code: "fixture_skills_require_demo",
      detail: `test skills are demo-only; ${mode} requires the model planner`,
      hint: "pass --mode demo (dev only) or drop --skills test",
    });
  }
  if (mode !== "demo") {
    if (options.question === undefined) {
      throw new CliUsageError("research requires --question");
    }
    if (options.recipeDigest === undefined) {
      throw new CliUsageError("research requires --recipe-digest");
    }
  }

  let connected: ConnectedPlatform | null = null;
  let sessionError: { code: string; message: string } | null = null;
  const handshakeProject = options.project ?? profile.get("project_id");
  if (mode !== "demo") {
    try {
      connected = await connectPlatform({
        profile,
        mode: mode === "product" ? "product" : "acceptance",
        ...(handshakeProject === null ? {} : { projectId: handshakeProject }),
        client: { name: "rph-cli", version: CLI_VERSION },
        ...(dependencies.createTransport === undefined
          ? {}
          : { createTransport: dependencies.createTransport }),
      });
    } catch (error) {
      sessionError = {
        code:
          typeof error === "object" && error !== null && "code" in error
            ? String((error as { code?: unknown }).code)
            : "platform_connect_failed",
        message: error instanceof Error ? error.message : String(error),
      };
    }
  }

  try {
    const science: ScienceInput =
      options.science === undefined
        ? { seed: null, initialInput: null, issues: [] }
        : readScienceFile(options.science);
    refusals.push(...science.issues);

    const parts = await defaultParts(
      {
        environment,
        mode,
        profile,
        session: connected === null ? null : connected.session,
      },
      dependencies,
    );
    const effectiveParts: CliRuntimeParts = {
      ...parts,
      ...(science.seed === null ? {} : { scienceSeed: science.seed }),
      ...(science.initialInput === null || science.initialInput === undefined
        ? {}
        : { initialInput: science.initialInput }),
    };

    const writer = effectiveParts.writer ?? null;
    const readiness: ComposeRuntimeInput = {
      mode,
      environment,
      profile,
      skills: effectiveParts.skills ?? null,
      session: connected === null ? null : connected.session,
      sessionError,
      writer,
      artifactEvidencePath: profile.get("artifact_evidence_path"),
      scienceSeedPresent:
        mode === "demo"
          ? true
          : effectiveParts.scienceSeed !== null && effectiveParts.scienceSeed !== undefined,
      modelGateway: effectiveParts.modelGateway ?? null,
      evidenceReader: effectiveParts.evidenceReader ?? null,
      budgetUsage: effectiveParts.budgetUsage ?? null,
      proposalRef: effectiveParts.proposalRef ?? null,
      ...(effectiveParts.submitPayload === undefined
        ? {}
        : { submitPayload: effectiveParts.submitPayload }),
      ...(effectiveParts.submitIntent === undefined
        ? {}
        : { submitIntent: effectiveParts.submitIntent }),
    };
    const issues = collectReadinessIssues(readiness);
    if (refusals.length > 0 || issues.length > 0) {
      if (options.json === true) {
        printJson(io, {
          status: "incomplete",
          mode,
          environment,
          refusals,
          issues,
        });
      } else {
        io.stderr(`rph research: ${mode} composition is incomplete`);
        for (const line of refusalLines(refusals, issues)) io.stderr(line);
      }
      return EXIT_INCOMPLETE;
    }

    const runtime = composeResearchRuntime({
      ...readiness,
      ...(effectiveParts.adapters === undefined ? {} : { adapters: effectiveParts.adapters }),
    });
    if (mode !== "demo") {
      // A product run refuses the fixture planner even if it reached this point.
      assertProductionSkills(runtime.skills);
    }
    const input = supervisorInputFor(
      options,
      mode,
      effectiveParts,
      profile.get("project_id"),
    );
    const result = await runSupervisor(
      mode === "demo" ? input : { ...input, require_model_gateway: true },
      runtime.ports,
      runtime.skills,
    );
    const summary = resultSummary(result);
    if (options.json === true) {
      printJson(io, {
        status: result.status,
        mode,
        environment,
        evidence_kind: runtime.evidence_kind,
        demo: mode === "demo",
        cli_version: CLI_VERSION,
        result: summary,
      });
    } else {
      io.stdout(
        `rph research: mode=${mode} environment=${environment} evidence_kind=${runtime.evidence_kind}`,
      );
      io.stdout(`  status=${result.status} stop_reason=${result.stop_reason}`);
      io.stdout(
        `  rounds=${result.rounds_completed} submits=${result.submits} reports=${result.reports.length}`,
      );
      if (mode === "demo") {
        io.stdout("  DEMO (fixture) — not a scientific result, not platform evidence");
      }
    }
    return EXIT_OK;
  } catch (error) {
    return reportFailure(io, options, error, EXIT_FAILED);
  } finally {
    if (connected !== null) {
      await connected.close();
    }
  }
}

function reportFailure(io: CliIo, options: CliOptions, error: unknown, code: number): number {
  if (error instanceof ProductCompositionError) {
    if (options.json === true) {
      printJson(io, {
        status: "incomplete",
        mode: error.mode,
        refusals: [],
        issues: error.issues,
      });
    } else {
      io.stderr(`rph: ${error.mode} composition is incomplete`);
      for (const issue of error.issues) {
        io.stderr(`  - ${issue.code} [${issue.requirement}]: ${issue.detail} (${issue.hint})`);
      }
    }
    return EXIT_INCOMPLETE;
  }
  const typedCode =
    typeof error === "object" && error !== null && "code" in error
      ? String((error as { code?: unknown }).code)
      : "failed";
  const message = error instanceof Error ? error.message : String(error);
  if (options.json === true) {
    printJson(io, { status: "error", code: typedCode, message });
  } else {
    io.stderr(`rph: ${typedCode}: ${message}`);
  }
  return code;
}

async function runResume(
  options: CliOptions,
  io: CliIo,
  dependencies: CliDependencies,
): Promise<number> {
  if (options.proposal === undefined) {
    throw new CliUsageError("resume requires --proposal");
  }
  if (options.planDigest === undefined) {
    throw new CliUsageError("resume requires --plan-digest");
  }
  const environment = environmentFor(options);
  const profile = resolveRuntimeProfile(environment, overrideEnv(io.env, options));
  const mode: CompositionMode = options.mode ?? "product";
  if (mode === "demo") {
    const message = "resume addresses a real research saga; a demo has no saga to resume";
    if (options.json === true) {
      printJson(io, { status: "incomplete", code: "demo_resume_refused", message });
    } else {
      io.stderr(`rph resume: demo_resume_refused: ${message}`);
    }
    return EXIT_INCOMPLETE;
  }
  let connected: ConnectedPlatform;
  try {
    connected = await connectPlatform({
      profile,
      mode: mode === "product" ? "product" : "acceptance",
      ...(options.project === undefined ? {} : { projectId: options.project }),
      client: { name: "rph-cli", version: CLI_VERSION },
      ...(dependencies.createTransport === undefined
        ? {}
        : { createTransport: dependencies.createTransport }),
    });
  } catch (error) {
    return reportFailure(io, options, error, EXIT_FAILED);
  }
  try {
    const outcome = await resumeResearch(connected.session, {
      proposal_ref: options.proposal,
      plan_digest: options.planDigest,
      ...(options.task === undefined ? {} : { task_ref: options.task }),
      ...(options.cursor === undefined ? {} : { cursor: options.cursor }),
    });
    if (options.json === true) {
      printJson(io, { status: outcome.ok ? "resumed" : "refused", code: outcome.code, payload: outcome.payload });
    } else {
      io.stdout(`rph resume: ${outcome.ok ? "resumed" : "refused"} (${outcome.code})`);
      io.stdout(`  proposal=${options.proposal}`);
    }
    return outcome.ok ? EXIT_OK : EXIT_FAILED;
  } finally {
    await connected.close();
  }
}

function doctorReadiness(
  parts: CliRuntimeParts | null,
  profile: ResolvedRuntimeProfile,
): DoctorReadinessFacts {
  return {
    skillsKind: parts?.skills?.planner_kind ?? null,
    writerKind: parts?.writer?.kind ?? null,
    modelGateway: parts?.modelGateway !== null && parts?.modelGateway !== undefined,
    artifactEvidencePath: profile.get("artifact_evidence_path") !== null,
    scienceSeed: parts?.scienceSeed !== null && parts?.scienceSeed !== undefined,
  };
}

async function runDoctorCommand(
  options: CliOptions,
  io: CliIo,
  dependencies: CliDependencies,
): Promise<number> {
  const environment = options.environment ?? "dev";
  const env = overrideEnv(io.env, options);
  const profile = resolveRuntimeProfile(environment, env);
  const parts = await dependencies.parts?.({
    environment,
    mode: "product",
    profile,
    session: null,
  });
  const connect =
    options.connect === true
      ? async (): Promise<ConnectedPlatform> =>
          connectPlatform({
            profile,
            mode: "acceptance",
            ...(options.project === undefined ? {} : { projectId: options.project }),
            client: { name: "rph-doctor", version: CLI_VERSION },
            ...(dependencies.createTransport === undefined
              ? {}
              : { createTransport: dependencies.createTransport }),
          })
      : null;
  const report: DoctorReport = await runDoctor({
    environment,
    env,
    productVersion: CLI_VERSION,
    ...(connect === null ? {} : { connect }),
    readiness: doctorReadiness(parts ?? null, profile),
  });
  if (options.json === true) {
    printJson(io, report);
  } else {
    io.stdout(
      `rph doctor (${report.mode}, ${report.environment}): ${report.status} — product ${report.product_version}, node ${report.node_version}`,
    );
    for (const entry of report.checks) {
      io.stdout(`  [${entry.status}] ${entry.id}: ${entry.detail}`);
    }
    for (const setting of report.settings) {
      if (setting.secret) {
        io.stdout(`  ${setting.name}: ${setting.value ?? "<unset>"} (${setting.env})`);
      }
    }
  }
  return report.status === "ok" ? EXIT_OK : EXIT_FAILED;
}

/**
 * Dispatch one invocation.
 *
 * Never calls `process.exit`: the caller (`main.ts`) assigns `process.exitCode`,
 * so a test can run any command in-process.
 */
export async function main(
  argv: readonly string[],
  dependencies: CliDependencies = {},
  io: CliIo = processIo,
): Promise<number> {
  let options: CliOptions;
  try {
    options = parseArgs(argv);
  } catch (error) {
    if (error instanceof CliUsageError) {
      io.stderr(`rph: ${error.message}\n\n${renderInformation({ command: "help" })}`);
      return EXIT_USAGE;
    }
    throw error;
  }

  try {
    switch (options.command) {
      case "help":
      case "version":
      case "profile":
        io.stdout(renderInformation(options).replace(/\n$/, ""));
        return EXIT_OK;
      case "doctor":
        return await runDoctorCommand(options, io, dependencies);
      case "resume":
        return await runResume(options, io, dependencies);
      case "research":
        return await runResearch(options, io, dependencies);
    }
  } catch (error) {
    if (error instanceof CliUsageError) {
      io.stderr(`rph: ${error.message}\n\n${renderInformation({ command: "help" })}`);
      return EXIT_USAGE;
    }
    return reportFailure(io, options, error, EXIT_FAILED);
  }
}
