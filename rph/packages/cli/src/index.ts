/**
 * RPH CLI entry point.
 *
 * At this stage the CLI only parses arguments and prints help/usage. It must
 * not start a Controller, spawn processes, open sockets, or execute
 * scientific software; launching the frozen DSH client is a later task.
 */

import {
  BUNDLE_PROFILE_NAMES,
  DSH_MCP_CLIENT_PLUGIN,
  type BundleProfileName,
} from "@rph/dsh-bundle";

/** CLI version placeholder until the release task wires a real version. */
export const CLI_VERSION = "0.0.0";

/** Commands the parser recognises without side effects. */
export type CliCommand = "help" | "version" | "profile";

/** Parsed CLI options. */
export interface CliOptions {
  readonly command: CliCommand;
  readonly profile?: BundleProfileName;
}

/** Usage error; `main()` maps it to a non-zero exit without side effects. */
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

/** Pure argument parser. Throws `CliUsageError` on invalid input. */
export function parseArgs(argv: readonly string[]): CliOptions {
  let command: CliCommand = "help";
  let profile: BundleProfileName | undefined;

  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === undefined) {
      continue;
    }
    switch (arg) {
      case "--help":
      case "-h":
        command = "help";
        break;
      case "--version":
      case "-V":
        command = "version";
        break;
      case "--profile": {
        const value = argv[index + 1];
        if (value === undefined || value.startsWith("-")) {
          throw new CliUsageError("--profile requires a value");
        }
        profile = parseProfile(value);
        command = "profile";
        index += 1;
        break;
      }
      default:
        throw new CliUsageError(`unknown argument "${arg}"`);
    }
  }

  return profile === undefined ? { command } : { command, profile };
}

/** Static help text; no runtime discovery and no process inspection. */
export function usage(): string {
  return [
    "rph — ACP 2.0 experience client",
    "",
    "Usage:",
    "  rph --help",
    "  rph --version",
    "  rph --profile <web|headless>",
    "",
    `Profiles: ${BUNDLE_PROFILE_NAMES.join(", ")}`,
    `MCP plugin: ${DSH_MCP_CLIENT_PLUGIN}`,
    "This build only prints help and never starts a Controller or executes science.",
    "",
  ].join("\n");
}

/**
 * Print help/version/profile information.
 *
 * @returns process exit code (0 for a clean print, 2 for a usage error).
 */
export function main(argv: readonly string[]): number {
  let options: CliOptions;
  try {
    options = parseArgs(argv);
  } catch (error) {
    if (error instanceof CliUsageError) {
      process.stderr.write(`rph: ${error.message}\n\n${usage()}`);
      return 2;
    }
    throw error;
  }

  switch (options.command) {
    case "version":
      process.stdout.write(`rph ${CLI_VERSION}\n`);
      return 0;
    case "profile":
      process.stdout.write(`${options.profile ?? ""}\n`);
      return 0;
    case "help":
      process.stdout.write(usage());
      return 0;
  }
}

/* c8 ignore next 3 -- direct-execution shim; tests call main() explicitly */
if (process.argv[1] !== undefined && import.meta.url === `file://${process.argv[1]}`) {
  process.exitCode = main(process.argv.slice(2));
}
