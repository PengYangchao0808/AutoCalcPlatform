/**
 * Portable runtime profiles (M3-02 / G17).
 *
 * An RPH deployment has three environments — `dev`, `acceptance`, `release` —
 * and they differ only in **which parameters are required** and in the
 * fail-fast posture. Every host-specific value (interpreter, cwd, run root,
 * config, project, model, writer, budget) is a *parameter name*, resolved from
 * the environment at start-up:
 *
 *   - the profile itself contains **no absolute interpreter path**, **no
 *     checkout cwd**, **no acceptance run root** and **no plaintext key**;
 *   - a secret is only ever an environment reference and is never echoed by the
 *     doctor (see `renderSecret`);
 *   - a required parameter that is absent is a typed `missing_setting`, never a
 *     silent fallback to a developer machine layout.
 *
 * `RUNTIME_PROFILE_MATRIX` is the single source of truth for CLI, doctor and
 * the tests; `resolveRuntimeProfile` is the only resolver.
 */

export const RUNTIME_ENVIRONMENTS = Object.freeze([
  "dev",
  "acceptance",
  "release",
] as const);

export type RuntimeEnvironment = (typeof RUNTIME_ENVIRONMENTS)[number];

export const RUNTIME_SETTING_NAMES = Object.freeze([
  "controller_url",
  "interpreter",
  "working_directory",
  "run_root",
  "config_path",
  "project_id",
  "permission_scope",
  "artifact_evidence_path",
  "research_writer",
  "model_provider",
  "model_id",
  "model_requests_budget",
  "controller_api_key",
] as const);

export type RuntimeSettingName = (typeof RUNTIME_SETTING_NAMES)[number];

/** One parameter definition: an environment variable name plus its policy. */
export interface RuntimeSettingSpec {
  /** Environment variable that supplies the value (never the value itself). */
  readonly env: string;
  readonly required: boolean;
  /** A credential: resolved from the environment, masked in every report. */
  readonly secret?: boolean;
  /**
   * Portable fallback. Only non-host-specific values may carry one: a command
   * name (`python3`) or a policy number, never a path, a cwd or a key.
   */
  readonly default?: string;
  readonly description: string;
}

export interface RuntimeProfileSpec {
  readonly environment: RuntimeEnvironment;
  /** `demo` is a labelled fixture run; a product environment forbids it. */
  readonly allow_demo: boolean;
  /** The platform model gateway must be present (no local settlement). */
  readonly require_model_gateway: boolean;
  /** A research writer must be injected (no in-memory store in a product run). */
  readonly require_research_writer: boolean;
  /** An incomplete P1/P2 tool surface is a startup refusal. */
  readonly fail_closed_tool_surface: boolean;
  readonly settings: Readonly<Record<RuntimeSettingName, RuntimeSettingSpec>>;
}

export class RuntimeProfileError extends Error {
  readonly code: string;

  constructor(code: string, message: string) {
    super(`${code}: ${message}`);
    this.name = "RuntimeProfileError";
    this.code = code;
  }
}

const BASE_SETTINGS: Readonly<Record<RuntimeSettingName, RuntimeSettingSpec>> =
  Object.freeze({
    controller_url: {
      env: "ACP2_CONTROLLER_URL",
      required: false,
      description: "Controller base URL the MCP stdio adapter talks to",
    },
    interpreter: {
      env: "RPH_INTERPRETER",
      required: false,
      description: "Python interpreter *name* or host-supplied path for `acp mcp serve`",
    },
    working_directory: {
      env: "RPH_WORKING_DIRECTORY",
      required: false,
      description: "cwd of the MCP child (host supplied; never a checkout default)",
    },
    run_root: {
      env: "ACP_RUN_ROOT",
      required: false,
      description: "Controller run root (host supplied; never an acceptance literal)",
    },
    config_path: {
      env: "ACP_CONFIG",
      required: false,
      description: "Controller config file (host supplied)",
    },
    project_id: {
      env: "RPH_PROJECT",
      required: false,
      description: "Default project scope of this deployment",
    },
    permission_scope: {
      env: "RPH_PERMISSION_SCOPE",
      required: false,
      description: "Platform permission scope requested by this client",
    },
    artifact_evidence_path: {
      env: "RPH_ARTIFACT_EVIDENCE_PATH",
      required: false,
      description: "Where the platform publishes evidence artifacts for this deployment",
    },
    research_writer: {
      env: "RPH_RESEARCH_WRITER",
      required: false,
      description: "Research writer backend name (the writer itself is injected)",
    },
    model_provider: {
      env: "RPH_MODEL_PROVIDER",
      required: false,
      description: "Model provider routed through the Controller model gateway",
    },
    model_id: {
      env: "RPH_MODEL_ID",
      required: false,
      description: "Model identifier routed through the Controller model gateway",
    },
    model_requests_budget: {
      env: "RPH_MODEL_REQUESTS",
      required: false,
      description: "Planning model-request allowance of this deployment",
    },
    controller_api_key: {
      env: "RPH_CONTROLLER_API_KEY",
      required: false,
      secret: true,
      description: "Controller credential (environment reference only, never printed)",
    },
  });

function profile(
  environment: RuntimeEnvironment,
  options: {
    readonly allow_demo: boolean;
    readonly require_model_gateway: boolean;
    readonly require_research_writer: boolean;
    readonly fail_closed_tool_surface: boolean;
    readonly required: readonly RuntimeSettingName[];
    readonly defaults?: Readonly<Partial<Record<RuntimeSettingName, string>>>;
  },
): RuntimeProfileSpec {
  const settings = {} as Record<RuntimeSettingName, RuntimeSettingSpec>;
  for (const name of RUNTIME_SETTING_NAMES) {
    const base = BASE_SETTINGS[name];
    const fallback = options.defaults?.[name];
    settings[name] = Object.freeze(
      fallback === undefined
        ? { ...base, required: options.required.includes(name) }
        : { ...base, required: options.required.includes(name), default: fallback },
    );
  }
  return Object.freeze({
    environment,
    allow_demo: options.allow_demo,
    require_model_gateway: options.require_model_gateway,
    require_research_writer: options.require_research_writer,
    fail_closed_tool_surface: options.fail_closed_tool_surface,
    settings: Object.freeze(settings),
  });
}

/**
 * The three environments.
 *
 * `dev` is the only one that may run the labelled demo, and even it requires an
 * explicit `--mode demo`. `acceptance` and `release` refuse fixtures, require a
 * research writer and a model gateway, and fail closed on an incomplete tool
 * surface. No environment carries a host path or a key: `working_directory`,
 * `run_root`, `config_path`, `artifact_evidence_path`, `model_*` and the
 * secret have no default anywhere.
 */
export const RUNTIME_PROFILE_MATRIX: Readonly<
  Record<RuntimeEnvironment, RuntimeProfileSpec>
> = Object.freeze({
  dev: profile("dev", {
    allow_demo: true,
    require_model_gateway: false,
    require_research_writer: false,
    fail_closed_tool_surface: false,
    required: [],
    defaults: { interpreter: "python3", model_requests_budget: "8" },
  }),
  acceptance: profile("acceptance", {
    allow_demo: false,
    require_model_gateway: true,
    require_research_writer: true,
    fail_closed_tool_surface: true,
    required: [
      "controller_url",
      "interpreter",
      "working_directory",
      "run_root",
      "config_path",
      "project_id",
      "permission_scope",
      "artifact_evidence_path",
      "research_writer",
      "model_requests_budget",
    ],
  }),
  release: profile("release", {
    allow_demo: false,
    require_model_gateway: true,
    require_research_writer: true,
    fail_closed_tool_surface: true,
    required: [
      "controller_url",
      "interpreter",
      "working_directory",
      "run_root",
      "config_path",
      "project_id",
      "permission_scope",
      "artifact_evidence_path",
      "research_writer",
      "model_provider",
      "model_id",
      "model_requests_budget",
    ],
  }),
});

/** A resolved setting: the value, where it came from, and its secrecy. */
export interface ResolvedSetting {
  readonly name: RuntimeSettingName;
  readonly env: string;
  readonly value: string | null;
  readonly source: "env" | "default" | "absent";
  readonly secret: boolean;
  readonly required: boolean;
}

export interface ProfileIssue {
  readonly code: "environment_unknown" | "missing_setting";
  readonly setting: RuntimeSettingName | null;
  readonly env: string | null;
  readonly detail: string;
}

export interface ResolvedRuntimeProfile {
  readonly environment: RuntimeEnvironment;
  readonly spec: RuntimeProfileSpec;
  readonly settings: Readonly<Record<RuntimeSettingName, ResolvedSetting>>;
  readonly missing: readonly RuntimeSettingName[];
  readonly issues: readonly ProfileIssue[];
  /** Resolved value of one setting (`null` when absent). */
  get(name: RuntimeSettingName): string | null;
}

/** Narrow an arbitrary string to a supported environment. */
export function isRuntimeEnvironment(value: string): value is RuntimeEnvironment {
  return (RUNTIME_ENVIRONMENTS as readonly string[]).includes(value);
}

/**
 * Resolve one environment against the process environment.
 *
 * @throws RuntimeProfileError for an unknown environment name.
 */
export function resolveRuntimeProfile(
  environment: RuntimeEnvironment,
  env: Readonly<Record<string, string | undefined>>,
): ResolvedRuntimeProfile {
  const spec = RUNTIME_PROFILE_MATRIX[environment];
  if (spec === undefined) {
    throw new RuntimeProfileError(
      "environment_unknown",
      `unknown runtime environment ${String(environment)} (expected ${RUNTIME_ENVIRONMENTS.join("|")})`,
    );
  }
  const settings = {} as Record<RuntimeSettingName, ResolvedSetting>;
  const missing: RuntimeSettingName[] = [];
  const issues: ProfileIssue[] = [];
  for (const name of RUNTIME_SETTING_NAMES) {
    const setting = spec.settings[name];
    const raw = env[setting.env];
    const fromEnv = typeof raw === "string" && raw.trim().length > 0 ? raw.trim() : null;
    const value = fromEnv ?? setting.default ?? null;
    const source: ResolvedSetting["source"] =
      fromEnv !== null ? "env" : setting.default !== undefined ? "default" : "absent";
    settings[name] = Object.freeze({
      name,
      env: setting.env,
      value,
      source,
      secret: setting.secret === true,
      required: setting.required,
    });
    if (value === null && setting.required) {
      missing.push(name);
      issues.push(
        Object.freeze({
          code: "missing_setting" as const,
          setting: name,
          env: setting.env,
          detail: `${environment} requires ${name} (set ${setting.env})`,
        }),
      );
    }
  }
  return Object.freeze({
    environment,
    spec,
    settings: Object.freeze(settings),
    missing: Object.freeze(missing),
    issues: Object.freeze(issues),
    get(name: RuntimeSettingName) {
      return settings[name].value;
    },
  });
}

/** Secret-safe rendering of one resolved setting (never the value). */
export function renderSecret(setting: ResolvedSetting): string {
  if (!setting.secret) {
    return setting.value ?? "<unset>";
  }
  return setting.value === null ? "<unset>" : "<redacted:env>";
}

/**
 * The secret-looking value guard shared by the doctor and its tests.
 *
 * A report may name an environment variable, a scope or a status, but never
 * carry a material credential.
 */
export const SECRET_VALUE_RE =
  /(?:Bearer\s+[A-Za-z0-9._~+/=-]{8,})|(?:sk-[A-Za-z0-9]{16,})|(?:AKIA[0-9A-Z]{16})|(?:[A-Za-z0-9_-]{32,})/;

/** True when a value is recognizable credential material. */
export function looksLikeSecret(value: unknown): boolean {
  if (typeof value !== "string") {
    return false;
  }
  if (!SECRET_VALUE_RE.test(value)) {
    return false;
  }
  // Environment names, ids and digests are published identifiers, not secrets.
  return !/^(?:RPH|ACP|ACP2)_[A-Z0-9_]*$/.test(value) && !/^sha256:[0-9a-f]{64}$/.test(value);
}
