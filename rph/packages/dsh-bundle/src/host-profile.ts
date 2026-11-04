/**
 * Parameterised DSH host launch profile — a pure data/string builder.
 *
 * `profiles/web.cordis.yml` is a **frozen acceptance record**: it names one
 * machine's interpreter path, checkout and Controller URL. This module is the
 * portable replacement the DSH plugin host uses: the caller supplies every host
 * value, `buildHostProfile` validates and normalises it into a typed profile,
 * and `renderCordisPatch` emits the same Cordis patch shape from it.
 *
 * Nothing here touches the network, a process, the filesystem, a clock or a
 * random source, so the same input always yields a deep-equal profile and
 * byte-identical patch text.
 */

/** Schema tag of the profile object this module returns. */
export const HOST_PROFILE_SCHEMA = "acp2.dsh.host-profile.v1";
/** Schema tag written into the generated Cordis patch comment header. */
export const HOST_PROFILE_PATCH_SCHEMA = "acp2.dsh.cordis-patch.v1";
/** Default MCP `serverName`; the model sees tools as `mcp__<serverName>__<raw>`. */
export const DEFAULT_SERVER_NAME = "acp";
/** Default per-tool-call budget, in milliseconds. */
export const DEFAULT_TOOL_CALL_TIMEOUT_MS = 120_000;

/** Official DSH MCP client plugin (mirrors `DSH_MCP_CLIENT_PLUGIN` in index). */
const DSH_MCP_CLIENT_PLUGIN_NAME = "@deepseek-ai/dsh-mcp-client";

const DEFAULT_LOG_LEVEL = "INFO";
const DEFAULT_CONTROLLER_URL_ENV = "ACP_CONTROLLER_URL";
const DEFAULT_RUN_ROOT_ENV = "ACP_RUN_ROOT";
const DEFAULT_CONFIG_ENV = "ACP_CONFIG";
const DEFAULT_TOOLS_ROOT_ENV = "ACP2_TOOLS";
const DEFAULT_LOG_LEVEL_ENV = "ACP2_MCP_LOG_LEVEL";

/** Argv atoms of the launch vector, spelled once (order is asserted in tests). */
const ARG_MODULE = "-m";
const ARG_PROGRAM = "acp";
const SUBCOMMAND_MCP = "mcp";
const SUBCOMMAND_SERVE = "serve";
const FLAG_TRANSPORT = "--transport";
const ARG_STDIO = "stdio";
const FLAG_CONTROLLER = "--controller";
const FLAG_LOG_LEVEL = "--log-level";

const SERVER_NAME_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;
const LOG_LEVEL_PATTERN = /^[A-Za-z][A-Za-z0-9_-]*$/;
const ENV_NAME_PATTERN = /^[A-Za-z_][A-Za-z0-9_]*$/;
const CONTROL_CHARS = /[\r\n\u0000]/;
/** `scheme://userinfo@host` — a credential embedded in a value. */
const URL_USERINFO = /\/\/[^/\s?#]*@/;
/** A value that assigns a secret instead of naming the variable holding it. */
const SECRET_ASSIGNMENT = /^[A-Z0-9_]*(?:API_KEY|TOKEN|PASSWORD)=/;

/** Caller-supplied parameters for one DSH host launch profile. */
export interface HostProfileInput {
  controller: string;
  pythonInterpreter: string;
  cwd: string;
  runRoot: string;
  configPath?: string;
  toolsRoot?: string;
  serverName?: string;
  logLevel?: string;
  toolCallTimeoutMs?: number;
  controllerUrlEnv?: string;
  runRootEnv?: string;
  configEnv?: string;
  toolsRootEnv?: string;
  logLevelEnv?: string;
  /** Extra environment entries, merged last (ordered, de-duplicated). */
  env?: Record<string, string>;
}

/** One environment pair; `name` is the variable, never a credential value. */
export interface HostProfileEnvEntry {
  readonly name: string;
  readonly value: string;
}

/** Ordered environment block: the array order is part of the contract. */
export interface HostProfileEnv extends ReadonlyArray<HostProfileEnvEntry> {}

/** A validated, normalised, deterministic DSH host launch profile. */
export interface HostProfile {
  schema: typeof HOST_PROFILE_SCHEMA;
  serverName: string;
  transport: "stdio";
  command: string;
  args: readonly string[];
  cwd: string;
  env: HostProfileEnv;
  toolCallTimeoutMs: number;
}

/** Typed refusal: a stable `code` plus the offending `field`, never a bare `Error`. */
export class HostProfileError extends Error {
  readonly code: string;
  readonly field?: string;

  constructor(code: string, field: string | undefined, detail: string) {
    super(field === undefined ? `${code}: ${detail}` : `${code} (${field}): ${detail}`);
    this.name = "HostProfileError";
    this.code = code;
    if (field !== undefined) {
      this.field = field;
    }
  }
}

function fail(code: string, field: string, detail: string): never {
  throw new HostProfileError(code, field, detail);
}

function isRecord(value: unknown): value is Readonly<Record<string, unknown>> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function tryParseUrl(value: string): URL | null {
  try {
    return new URL(value);
  } catch {
    return null;
  }
}

function isBlank(value: unknown): value is undefined {
  return value === undefined;
}

/**
 * A path-ish value: non-empty, no newline/NUL, no embedded URL userinfo.
 * The value is otherwise used verbatim — resolving a caller's path is not this
 * module's decision.
 */
function requirePath(value: unknown, code: string, field: string): string {
  if (typeof value !== "string") fail(code, field, "must be a string");
  const trimmed = value.trim();
  if (trimmed.length === 0) fail(code, field, "must be a non-empty value");
  if (CONTROL_CHARS.test(trimmed)) fail(code, field, "must not contain a newline or NUL");
  if (URL_USERINFO.test(trimmed)) fail("credential_leak", field, "must not embed URL userinfo");
  return trimmed;
}

function optionalPath(value: unknown, field: string): string | undefined {
  return isBlank(value) ? undefined : requirePath(value, "invalid_input", field);
}

function validatedLabel(value: unknown, field: string, fallback: string, pattern: RegExp): string {
  if (isBlank(value)) return fallback;
  const text = requirePath(value, "invalid_input", field);
  if (!pattern.test(text)) fail("invalid_input", field, "must have the documented identifier form");
  return text;
}

/**
 * An absolute `http:`/`https:` Controller URL: no credentials, no query, no
 * fragment; a trailing path slash is normalised away so `http://h:1/` and
 * `http://h:1` are one profile.
 */
function validatedController(value: unknown): string {
  if (typeof value !== "string") fail("invalid_controller", "controller", "must be a string");
  const trimmed = value.trim();
  if (trimmed.length === 0) fail("invalid_controller", "controller", "must be a non-empty URL");
  if (CONTROL_CHARS.test(trimmed)) {
    fail("invalid_controller", "controller", "must not contain a newline or NUL");
  }
  const parsed = tryParseUrl(trimmed);
  if (parsed === null) fail("invalid_controller", "controller", "must be an absolute URL");
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    fail("invalid_controller", "controller", "must be an http(s) URL");
  }
  if (parsed.hostname.length === 0) fail("invalid_controller", "controller", "must name a host");
  if (parsed.username !== "" || parsed.password !== "") {
    fail("invalid_controller", "controller", "must not embed credentials");
  }
  if (parsed.search !== "") fail("invalid_controller", "controller", "must not carry a query");
  if (parsed.hash !== "") fail("invalid_controller", "controller", "must not carry a fragment");
  return `${parsed.protocol}//${parsed.host}${parsed.pathname.replace(/\/+$/, "")}`;
}

function validatedTimeout(value: unknown): number {
  if (isBlank(value)) return DEFAULT_TOOL_CALL_TIMEOUT_MS;
  if (
    typeof value !== "number" ||
    !Number.isInteger(value) ||
    value <= 0 ||
    value > Number.MAX_SAFE_INTEGER
  ) {
    fail("invalid_input", "toolCallTimeoutMs", "must be a positive integer of milliseconds");
  }
  return value;
}

function assertDistinctEnvNames(names: readonly string[]): void {
  const seen = new Map<string, string>();
  for (const name of names) {
    const previous = seen.get(name.toLowerCase());
    if (previous !== undefined) fail("duplicate_env", `env.${name}`, `collides with env.${previous}`);
    seen.set(name.toLowerCase(), name);
  }
}

/**
 * Merge the fixed pairs with caller entries: fixed order first (run root,
 * Controller URL, log level, tools root, config path), then caller names in
 * sorted order. A caller entry naming an existing variable **replaces its value
 * in place** and is never appended twice; two caller names differing only in
 * case are a `duplicate_env`.
 */
function buildEnv(fixed: readonly HostProfileEnvEntry[], extra: unknown): HostProfileEnv {
  if (extra !== undefined && !isRecord(extra)) {
    fail("invalid_input", "env", "must be an object of string values");
  }
  const order: string[] = [];
  const values = new Map<string, string>();
  for (const entry of fixed) {
    order.push(entry.name);
    values.set(entry.name, entry.value);
  }

  const entries: [string, unknown][] =
    extra === undefined ? [] : Object.entries(extra as Readonly<Record<string, unknown>>);
  entries.sort((left, right) => (left[0] < right[0] ? -1 : left[0] > right[0] ? 1 : 0));

  const seen = new Map<string, string>();
  for (const [name, value] of entries) {
    if (!ENV_NAME_PATTERN.test(name)) {
      fail("invalid_input", `env.${name}`, "must be a valid environment variable name");
    }
    if (typeof value !== "string") fail("invalid_input", `env.${name}`, "must be a string");
    if (CONTROL_CHARS.test(value)) {
      fail("invalid_input", `env.${name}`, "must not contain a newline or NUL");
    }
    const previous = seen.get(name.toLowerCase());
    if (previous !== undefined && previous !== name) {
      fail("duplicate_env", `env.${name}`, `collides with env.${previous}`);
    }
    seen.set(name.toLowerCase(), name);
    if (!values.has(name)) order.push(name);
    values.set(name, value);
  }

  const out: HostProfileEnvEntry[] = order.map((name) =>
    Object.freeze({ name, value: values.get(name) ?? "" }));
  return Object.freeze(out);
}

/**
 * Build one portable host profile.
 *
 * Pure and referentially transparent: no ambient state is read, the input is
 * never mutated, and the same input always produces a deep-equal result.
 */
export function buildHostProfile(input: HostProfileInput): HostProfile {
  if (!isRecord(input)) fail("invalid_input", "input", "must be an object");
  const controller = validatedController(input.controller);
  const command = requirePath(input.pythonInterpreter, "invalid_interpreter", "pythonInterpreter");
  const cwd = requirePath(input.cwd, "invalid_cwd", "cwd");
  const runRoot = requirePath(input.runRoot, "invalid_run_root", "runRoot");
  const configPath = optionalPath(input.configPath, "configPath");
  const toolsRoot = optionalPath(input.toolsRoot, "toolsRoot");
  const serverName = validatedLabel(
    input.serverName,
    "serverName",
    DEFAULT_SERVER_NAME,
    SERVER_NAME_PATTERN,
  );
  const logLevel = validatedLabel(input.logLevel, "logLevel", DEFAULT_LOG_LEVEL, LOG_LEVEL_PATTERN);
  const toolCallTimeoutMs = validatedTimeout(input.toolCallTimeoutMs);

  const name = {
    run: validatedLabel(input.runRootEnv, "runRootEnv", DEFAULT_RUN_ROOT_ENV, ENV_NAME_PATTERN),
    controller: validatedLabel(
      input.controllerUrlEnv, "controllerUrlEnv", DEFAULT_CONTROLLER_URL_ENV, ENV_NAME_PATTERN,
    ),
    logLevel: validatedLabel(
      input.logLevelEnv, "logLevelEnv", DEFAULT_LOG_LEVEL_ENV, ENV_NAME_PATTERN,
    ),
    tools: validatedLabel(
      input.toolsRootEnv, "toolsRootEnv", DEFAULT_TOOLS_ROOT_ENV, ENV_NAME_PATTERN,
    ),
    config: validatedLabel(input.configEnv, "configEnv", DEFAULT_CONFIG_ENV, ENV_NAME_PATTERN),
  };
  assertDistinctEnvNames([name.run, name.controller, name.logLevel, name.tools, name.config]);

  const fixed: HostProfileEnvEntry[] = [
    { name: name.run, value: runRoot },
    { name: name.controller, value: controller },
    { name: name.logLevel, value: logLevel },
  ];
  if (toolsRoot !== undefined) fixed.push({ name: name.tools, value: toolsRoot });
  if (configPath !== undefined) fixed.push({ name: name.config, value: configPath });

  const args = Object.freeze([
    ARG_MODULE,
    ARG_PROGRAM,
    SUBCOMMAND_MCP,
    SUBCOMMAND_SERVE,
    FLAG_TRANSPORT,
    ARG_STDIO,
    FLAG_CONTROLLER,
    controller,
    FLAG_LOG_LEVEL,
    logLevel,
  ]);

  const profile: HostProfile = {
    schema: HOST_PROFILE_SCHEMA,
    serverName,
    transport: "stdio",
    command,
    args,
    cwd,
    env: buildEnv(fixed, input.env),
    toolCallTimeoutMs,
  };
  return Object.freeze(profile);
}

/**
 * Refuse a profile that leaks a credential: the profile carries environment
 * variable **names** and caller paths, never a secret value, so an embedded URL
 * userinfo or a `TOKEN=`-style assignment is a `credential_leak`.
 */
export function assertPortableHostProfile(profile: HostProfile): void {
  for (const entry of profile.env) {
    const field = `env.${entry.name}`;
    if (URL_USERINFO.test(entry.value)) fail("credential_leak", field, "must not embed userinfo");
    if (SECRET_ASSIGNMENT.test(entry.value)) {
      fail("credential_leak", field, "must name the variable holding a secret, not its value");
    }
  }
  for (const [index, arg] of profile.args.entries()) {
    if (URL_USERINFO.test(arg)) fail("credential_leak", `args[${String(index)}]`, "no userinfo");
  }
}

/** A single-quoted YAML scalar; an embedded quote is doubled. */
function quoted(value: string): string {
  return `'${value.replaceAll("'", "''")}'`;
}

function isProfileList(value: unknown): value is readonly HostProfile[] {
  return Array.isArray(value);
}

/**
 * Render a Cordis patch document for one or more host profiles.
 *
 * Shape of `profiles/web.cordis.yml`: a top-level sequence whose first item is
 * `- insert:` with one row per profile (`id`, `name`, `config`). Every string is
 * a single-quoted scalar, the output is LF-only with no trailing whitespace and
 * ends with exactly one newline, so the text is deterministic per input.
 */
export function renderCordisPatch(
  profiles: readonly HostProfile[] | HostProfile,
  options?: { readonly extraRows?: readonly unknown[] },
): string {
  const list = isProfileList(profiles) ? profiles : [profiles];
  if (list.length === 0) {
    fail("invalid_input", "profiles", "at least one host profile is required");
  }

  const lines: string[] = [
    "# Generated DSH Cordis patch — do not edit by hand.",
    `# Schema: ${quoted(HOST_PROFILE_PATCH_SCHEMA)}`,
    `# Profile schema: ${quoted(HOST_PROFILE_SCHEMA)}`,
    "#",
    "# Apply with:",
    "#   dsh --profile <name> --patch <this file> --host 127.0.0.1 --port 0 --no-open",
    "#",
    "# Command, args, cwd and env come from the caller's HostProfileInput; this",
    "# file is regenerated, never copied from a host-specific acceptance record.",
    "",
    "- insert:",
  ];

  const ids = new Set<string>();
  for (const profile of list) {
    assertPortableHostProfile(profile);
    const id = `acp-mcp-${profile.serverName}`;
    if (ids.has(id)) fail("invalid_input", "profiles", `duplicate Cordis row id ${id}`);
    ids.add(id);
    lines.push(`    - id: ${quoted(id)}`);
    lines.push(`      name: ${quoted(DSH_MCP_CLIENT_PLUGIN_NAME)}`);
    lines.push("      config:");
    lines.push(`        serverName: ${quoted(profile.serverName)}`);
    lines.push(`        transport: ${quoted(profile.transport)}`);
    lines.push(`        command: ${quoted(profile.command)}`);
    lines.push("        args:");
    for (const arg of profile.args) lines.push(`          - ${quoted(arg)}`);
    lines.push(`        cwd: ${quoted(profile.cwd)}`);
    lines.push("        env:");
    for (const entry of profile.env) {
      lines.push(`          ${quoted(entry.name)}: ${quoted(entry.value)}`);
    }
    lines.push(`        toolCallTimeoutMs: ${String(profile.toolCallTimeoutMs)}`);
  }

  for (const row of options?.extraRows ?? []) {
    const serialised = JSON.stringify(row);
    lines.push(`- ${serialised === undefined ? "null" : serialised}`);
  }

  return `${lines.join("\n")}\n`;
}
