/**
 * RPH CLI entry point (M3-02a).
 *
 * `src/compose.ts` is the only composition root; `src/index.ts` (this file) owns
 * the pure argument parser, the usage text and the command dispatch. The CLI
 * imports **`@rph/dsh-bundle` only** — never `@rph/bridge` or `@rph/research` —
 * so the frozen dependency chain `cli -> dsh-bundle -> bridge -> research` stays
 * a single edge per hop. The one process seam (`acp mcp serve`) lives in the
 * bundle's named launcher; this file starts nothing by itself.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import {
  BUNDLE_PROFILE_NAMES,
  COMPOSITION_MODES,
  DSH_MCP_CLIENT_PLUGIN,
  RUNTIME_ENVIRONMENTS,
  isCompositionMode,
  isRuntimeEnvironment,
  type BundleProfileName,
  type CompositionMode,
  type RuntimeEnvironment,
} from "@rph/dsh-bundle";

/**
 * The product version: `packages/cli/package.json`, read at import time.
 *
 * There is deliberately no second version constant in this package.
 */
export function cliVersion(manifestUrl: URL = new URL("../package.json", import.meta.url)): string {
  const manifest = JSON.parse(readFileSync(fileURLToPath(manifestUrl), "utf8")) as {
    version?: unknown;
  };
  if (typeof manifest.version !== "string" || manifest.version.length === 0) {
    throw new Error(`${fileURLToPath(manifestUrl)} has no version`);
  }
  return manifest.version;
}

export const CLI_VERSION = cliVersion();

/** Commands the parser recognises; every command is explicit. */
export const CLI_COMMANDS = Object.freeze([
  "help",
  "version",
  "profile",
  "research",
  "resume",
  "doctor",
] as const);

export type CliCommand = (typeof CLI_COMMANDS)[number];

/** Skill bindings a caller may ask for; `test` is demo-only. */
export const CLI_SKILL_MODES = Object.freeze(["model", "test"] as const);
export type CliSkills = (typeof CLI_SKILL_MODES)[number];

/** Parsed CLI options (pure data; no side effect was taken). */
export interface CliOptions {
  readonly command: CliCommand;
  readonly profile?: BundleProfileName;
  readonly environment?: RuntimeEnvironment;
  readonly mode?: CompositionMode;
  readonly skills?: CliSkills;
  readonly controller?: string;
  readonly project?: string;
  readonly research?: string;
  readonly question?: string;
  readonly science?: string;
  readonly recipeDigest?: string;
  readonly proposal?: string;
  readonly planDigest?: string;
  readonly task?: string;
  readonly cursor?: string;
  readonly attempt?: number;
  readonly maxRounds?: number;
  readonly connect?: boolean;
  readonly json?: boolean;
}

/** Usage error; `main()` maps it to exit code 2 without side effects. */
export class CliUsageError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "CliUsageError";
  }
}

function parseProfile(value: string): BundleProfileName {
  const match = BUNDLE_PROFILE_NAMES.find((name) => name === value);
  if (match === undefined) {
    throw new CliUsageError(
      `unknown profile "${value}" (expected ${BUNDLE_PROFILE_NAMES.join("|")})`,
    );
  }
  return match;
}

function parseEnvironment(value: string): RuntimeEnvironment {
  if (!isRuntimeEnvironment(value)) {
    throw new CliUsageError(
      `unknown environment "${value}" (expected ${RUNTIME_ENVIRONMENTS.join("|")})`,
    );
  }
  return value;
}

function parseMode(value: string): CompositionMode {
  if (!isCompositionMode(value)) {
    throw new CliUsageError(
      `unknown mode "${value}" (expected ${COMPOSITION_MODES.join("|")})`,
    );
  }
  return value;
}

function parseSkills(value: string): CliSkills {
  const match = CLI_SKILL_MODES.find((name) => name === value);
  if (match === undefined) {
    throw new CliUsageError(`unknown skills "${value}" (expected ${CLI_SKILL_MODES.join("|")})`);
  }
  return match;
}

function parseInteger(flag: string, value: string): number {
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed < 0) {
    throw new CliUsageError(`${flag} requires a non-negative integer`);
  }
  return parsed;
}

const VALUE_FLAGS = new Set([
  "--profile",
  "--environment",
  "--mode",
  "--skills",
  "--controller",
  "--project",
  "--research",
  "--question",
  "--science",
  "--recipe-digest",
  "--proposal",
  "--plan-digest",
  "--task",
  "--cursor",
  "--attempt",
  "--max-rounds",
]);

/** Pure argument parser. Throws `CliUsageError` on invalid input. */
export function parseArgs(argv: readonly string[]): CliOptions {
  const options: {
    -readonly [K in keyof CliOptions]: CliOptions[K];
  } = { command: "help" };
  let explicitCommand = false;

  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === undefined) {
      continue;
    }
    switch (arg) {
      case "--help":
      case "-h":
        options.command = "help";
        explicitCommand = false;
        break;
      case "--version":
      case "-V":
        options.command = "version";
        explicitCommand = false;
        break;
      case "help":
      case "version":
      case "profile":
      case "research":
      case "resume":
      case "doctor":
        options.command = arg;
        explicitCommand = true;
        break;
      case "--json":
        options.json = true;
        break;
      case "--connect":
        options.connect = true;
        break;
      default: {
        if (!VALUE_FLAGS.has(arg)) {
          throw new CliUsageError(`unknown argument "${arg}"`);
        }
        const value = argv[index + 1];
        if (value === undefined || value.startsWith("-")) {
          throw new CliUsageError(`${arg} requires a value`);
        }
        index += 1;
        switch (arg) {
          case "--profile":
            options.profile = parseProfile(value);
            if (!explicitCommand) options.command = "profile";
            break;
          case "--environment":
            options.environment = parseEnvironment(value);
            break;
          case "--mode":
            options.mode = parseMode(value);
            break;
          case "--skills":
            options.skills = parseSkills(value);
            break;
          case "--controller":
            options.controller = value;
            break;
          case "--project":
            options.project = value;
            break;
          case "--research":
            options.research = value;
            break;
          case "--question":
            options.question = value;
            break;
          case "--science":
            options.science = value;
            break;
          case "--recipe-digest":
            options.recipeDigest = value;
            break;
          case "--proposal":
            options.proposal = value;
            break;
          case "--plan-digest":
            options.planDigest = value;
            break;
          case "--task":
            options.task = value;
            break;
          case "--cursor":
            options.cursor = value;
            break;
          case "--attempt":
            options.attempt = parseInteger(arg, value);
            break;
          case "--max-rounds":
            options.maxRounds = parseInteger(arg, value);
            break;
          default:
            throw new CliUsageError(`unknown argument "${arg}"`);
        }
      }
    }
  }

  return Object.freeze(options) as CliOptions;
}

/** Static help text; no runtime discovery and no process inspection. */
export function usage(): string {
  return [
    "rph — ACP 2.0 experience client",
    "",
    "Usage:",
    "  rph --help | --version",
    "  rph --profile <web|headless>",
    "  rph research --question <text> --recipe-digest <sha256:..> [options]",
    "  rph resume --proposal <ref> --plan-digest <sha256:..> [options]",
    "  rph doctor [--connect] [--environment <dev|acceptance|release>] [--json]",
    "",
    `Profiles: ${BUNDLE_PROFILE_NAMES.join(", ")}`,
    `Environments: ${RUNTIME_ENVIRONMENTS.join(" | ")}`,
    `Modes: ${COMPOSITION_MODES.join(" | ")} (demo is a labelled fixture)`,
    `MCP plugin: ${DSH_MCP_CLIENT_PLUGIN}`,
    "",
    "research options:",
    "  --mode <product|acceptance|demo>  composition mode (default product)",
    "  --skills <model|test>             test skills are demo-only",
    "  --controller <url>                overrides ACP2_CONTROLLER_URL",
    "  --science <file>                  seed, input, submission + planning_budget (JSON)",
    "  --project <id> --research <id> --max-rounds <n>",
    "resume options:",
    "  --proposal <ref> --plan-digest <sha256:..> [--task <job>] [--cursor <c>]",
    "environment: every host value comes from the environment",
    `  (${RUNTIME_ENVIRONMENTS.join("/")} differ only in required parameters)`,
    "",
    "No command starts a Controller: `research`/`doctor --connect` use the single",
    "named launcher for `acp mcp serve` and never execute science by themselves.",
    "",
  ].join("\n");
}

/** Print help/version/profile information (pure; used by the parser tests). */
export function renderInformation(options: CliOptions): string {
  switch (options.command) {
    case "version":
      return `rph ${CLI_VERSION}\n`;
    case "profile":
      return `${options.profile ?? ""}\n`;
    case "help":
    default:
      return usage();
  }
}

// ---------------------------------------------------------------------------
// Command runtime
// ---------------------------------------------------------------------------
//
// Re-exported from `src/compose.ts` (the only composition root). The cycle is
// benign in ESM: `compose.ts` only reads `parseArgs`/`CLI_VERSION` when a
// command runs, never while the module graph is initialising.

export {
  EXIT_FAILED,
  EXIT_INCOMPLETE,
  EXIT_OK,
  EXIT_USAGE,
  main,
  processIo,
  type CliDependencies,
  type CliIo,
  type CliPartContext,
  type CliRuntimeParts,
} from "./compose.js";
