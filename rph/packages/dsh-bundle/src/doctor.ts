/**
 * Doctor: static and `--connect` diagnosis (M3-02 / G17).
 *
 * Two modes, one report shape:
 *
 *   - **static** (`mode: "static"`) inspects the resolved profile, the version
 *     pair, the writer/budget/model configuration and the secret handling. It
 *     never opens a socket or starts a process.
 *   - **connect** (`mode: "connect"`) additionally opens a real platform session
 *     through the named launcher and reports the handshake, permissions, the
 *     research-extension answer and the observed tool surface.
 *
 * Honesty rules: a check that was not executed is `skip` (never `pass`), a
 * strict environment (`acceptance`/`release`) treats a missing product
 * parameter as `fail`, and a credential is always rendered as
 * `<redacted:env>` — the report is scanned for the real secret value before it
 * leaves this module.
 */

import { BRIDGE_EXTENSION_ID, BRIDGE_EXTENSION_VERSION } from "@rph/bridge";

import type { ConnectedPlatform } from "./compose.js";
import { REQUIRED_P1_TOOL_NAMES, REQUIRED_P2_TOOL_NAMES } from "./platform-session.js";
import { MCP_PROTOCOL_VERSION } from "./mcp-stdio.js";
import { BUNDLE_VERSION } from "./version.js";
import {
  RUNTIME_SETTING_NAMES,
  looksLikeSecret,
  renderSecret,
  resolveRuntimeProfile,
  type ResolvedRuntimeProfile,
  type RuntimeEnvironment,
  type RuntimeSettingName,
} from "./runtime-profile.js";

export const DOCTOR_SCHEMA = "acp2.rph.doctor.v1" as const;

export const DOCTOR_CHECKS = Object.freeze([
  "version",
  "paths",
  "handshake",
  "permissions",
  "extensions",
  "writer",
  "budget",
  "model",
  "secrets",
] as const);

export type DoctorCheckId = (typeof DOCTOR_CHECKS)[number];

export type DoctorCheckStatus = "pass" | "fail" | "skip";

export interface DoctorCheck {
  readonly id: DoctorCheckId;
  readonly status: DoctorCheckStatus;
  readonly detail: string;
  readonly params: Readonly<Record<string, unknown>>;
}

/** Secret-free projection of the resolved profile. */
export interface DoctorSettingView {
  readonly name: RuntimeSettingName;
  readonly env: string;
  readonly source: "env" | "default" | "absent";
  readonly required: boolean;
  readonly secret: boolean;
  readonly value: string | null;
}

export interface DoctorReport {
  readonly schema: typeof DOCTOR_SCHEMA;
  readonly mode: "static" | "connect";
  readonly environment: RuntimeEnvironment;
  readonly status: "ok" | "failed";
  readonly product_version: string;
  readonly bundle_version: string;
  readonly node_version: string;
  readonly settings: readonly DoctorSettingView[];
  readonly checks: readonly DoctorCheck[];
}

/** Facts the composition root already knows (never re-derived here). */
export interface DoctorReadinessFacts {
  readonly skillsKind?: string | null;
  readonly writerKind?: string | null;
  readonly modelGateway?: boolean;
  readonly artifactEvidencePath?: boolean;
  readonly scienceSeed?: boolean;
}

export interface DoctorInput {
  readonly environment: RuntimeEnvironment;
  readonly env: Readonly<Record<string, string | undefined>>;
  /** The consuming product's version (the CLI manifest). */
  readonly productVersion: string;
  readonly nodeVersion?: string;
  /** Opens a session; absent = static mode. */
  readonly connect?: (() => Promise<ConnectedPlatform>) | null;
  readonly readiness?: DoctorReadinessFacts;
}

function check(
  id: DoctorCheckId,
  status: DoctorCheckStatus,
  detail: string,
  params: Readonly<Record<string, unknown>> = {},
): DoctorCheck {
  return Object.freeze({ id, status, detail, params: Object.freeze({ ...params }) });
}

function settingView(profile: ResolvedRuntimeProfile): readonly DoctorSettingView[] {
  return Object.freeze(
    RUNTIME_SETTING_NAMES.map((name) => {
      const setting = profile.settings[name];
      return Object.freeze({
        name,
        env: setting.env,
        source: setting.source,
        required: setting.required,
        secret: setting.secret,
        value: setting.secret ? renderSecret(setting) : setting.value,
      });
    }),
  );
}

function nodeMajor(version: string): number {
  const match = /^v?(\d+)\./.exec(version.trim());
  return match === null ? 0 : Number(match[1]);
}

function isStrict(environment: RuntimeEnvironment): boolean {
  return environment !== "dev";
}

function writerCheck(
  strict: boolean,
  readiness: DoctorReadinessFacts,
): DoctorCheck {
  const kind = readiness.writerKind ?? null;
  if (kind === null) {
    // A missing non-strict prerequisite is not a pass: it is not checked.
    return strict
      ? check("writer", "fail", "no research writer is composed")
      : check("writer", "skip", "no research writer is composed");
  }
  if (kind.includes("fixture")) {
    return check("writer", "fail", `the composed writer is the ${kind} fixture`, { writer: kind });
  }
  return check("writer", "pass", `research writer: ${kind}`, { writer: kind });
}

/**
 * Run the doctor.
 *
 * Never throws for a failed check: a diagnostic tool reports. The caller maps
 * `status: "failed"` to a non-zero exit code.
 */
export async function runDoctor(input: DoctorInput): Promise<DoctorReport> {
  const strict = isStrict(input.environment);
  const nodeVersion = input.nodeVersion ?? process.version;
  const profile = resolveRuntimeProfile(input.environment, input.env);
  const readiness = input.readiness ?? {};
  const checks: DoctorCheck[] = [];

  // 1. version — one product version and a supported Node.
  const versionsAgree = input.productVersion === BUNDLE_VERSION;
  const nodeOk = nodeMajor(nodeVersion) >= 22;
  checks.push(
    check(
      "version",
      versionsAgree && nodeOk ? "pass" : "fail",
      versionsAgree
        ? `product ${input.productVersion}, node ${nodeVersion}`
        : `product ${input.productVersion} != bundle ${BUNDLE_VERSION}`,
      {
        product_version: input.productVersion,
        bundle_version: BUNDLE_VERSION,
        node_version: nodeVersion,
        node_supported: nodeOk,
      },
    ),
  );

  // 2. paths — required parameters resolve from the environment only.
  const supplied = RUNTIME_SETTING_NAMES.filter(
    (name) => profile.settings[name].value !== null,
  ).length;
  checks.push(
    check(
      "paths",
      profile.issues.length === 0 ? "pass" : "fail",
      profile.issues.length === 0
        ? `${supplied}/${RUNTIME_SETTING_NAMES.length} parameters supplied; ${profile.missing.length} required missing`
        : `missing required parameters: ${profile.issues
            .map((issue) => `${issue.setting}(${issue.env})`)
            .join(", ")}`,
      {
        missing: profile.missing,
        supplied,
        hidden_defaults: RUNTIME_SETTING_NAMES.filter(
          (name) =>
            profile.settings[name].source === "default" &&
            (profile.settings[name].name === "working_directory" ||
              profile.settings[name].name === "run_root" ||
              profile.settings[name].name === "config_path" ||
              profile.settings[name].name === "artifact_evidence_path"),
        ),
      },
    ),
  );

  // 3.–5. session-only checks: handshake, permissions, extensions.
  let connected: ConnectedPlatform | null = null;
  if (input.connect === null || input.connect === undefined) {
    checks.push(check("handshake", "skip", "not requested (use --connect)"));
    checks.push(check("permissions", "skip", "not requested (use --connect)"));
    checks.push(
      check(
        "extensions",
        "skip",
        `not requested (use --connect); expects ${BRIDGE_EXTENSION_ID}@${BRIDGE_EXTENSION_VERSION}`,
      ),
    );
  } else {
    try {
      connected = await input.connect();
      const protocolOk = connected.protocolVersion === MCP_PROTOCOL_VERSION;
      checks.push(
        check(
          "handshake",
          protocolOk ? "pass" : "fail",
          `protocol ${connected.protocolVersion || "<none>"} (client speaks ${MCP_PROTOCOL_VERSION})`,
          {
            protocol_version: connected.protocolVersion,
            server_info: connected.serverInfo,
            tools: connected.toolNames.length,
          },
        ),
      );
      const scope = profile.get("permission_scope");
      checks.push(
        check(
          "permissions",
          connected.extensionStatus === "refused" ? "fail" : "pass",
          `permission scope ${scope ?? "<unset>"}; handshake ${connected.extensionStatus}`,
          { permission_scope: scope, handshake: connected.extensionStatus },
        ),
      );
      const extensionOk = connected.extensionStatus === "negotiated";
      const observed = new Set(connected.toolNames);
      const missingP1 = REQUIRED_P1_TOOL_NAMES.filter((name) => !observed.has(name));
      const missingP2 = REQUIRED_P2_TOOL_NAMES.filter((name) => !observed.has(name));
      checks.push(
        check(
          "extensions",
          extensionOk && missingP1.length === 0 && missingP2.length === 0 ? "pass" : "fail",
          `${BRIDGE_EXTENSION_ID}@${BRIDGE_EXTENSION_VERSION}: ${connected.extensionStatus}; ` +
            `tools ${connected.toolNames.length} (p1 missing ${missingP1.length}, p2 missing ${missingP2.length})`,
          {
            status: connected.extensionStatus,
            missing_p1: missingP1,
            missing_p2: missingP2,
          },
        ),
      );
    } catch (error) {
      const code =
        typeof error === "object" && error !== null && "code" in error
          ? String((error as { code?: unknown }).code)
          : "connect_failed";
      const message = error instanceof Error ? error.message : String(error);
      checks.push(check("handshake", "fail", `${code}: ${message}`, { code }));
      checks.push(check("permissions", "skip", `no session (${code})`));
      checks.push(check("extensions", "skip", `no session (${code})`));
    } finally {
      if (connected !== null) {
        await connected.close();
      }
    }
  }

  // 6. writer.
  checks.push(writerCheck(strict, readiness));

  // 7. budget — a positive planning allowance in a strict environment.
  const budget = profile.get("model_requests_budget");
  const budgetValue = budget === null ? Number.NaN : Number(budget);
  if (budget === null) {
    checks.push(
      strict
        ? check("budget", "fail", "no model-request allowance configured", {
            env: profile.settings.model_requests_budget.env,
          })
        : check("budget", "skip", "no model-request allowance configured"),
    );
  } else if (!Number.isFinite(budgetValue) || budgetValue <= 0) {
    checks.push(check("budget", "fail", `model-request allowance is not positive: ${budget}`));
  } else {
    checks.push(
      check("budget", "pass", `model-request allowance ${String(budgetValue)}`, {
        model_requests: budgetValue,
      }),
    );
  }

  // 8. model configuration — provider/model + gateway, credential by reference.
  const provider = profile.get("model_provider");
  const modelId = profile.get("model_id");
  const gateway = readiness.modelGateway === true;
  const modelConfigured = provider !== null && modelId !== null;
  const modelStatus: DoctorCheckStatus =
    modelConfigured && gateway ? "pass" : strict ? "fail" : "skip";
  checks.push(
    check(
      "model",
      modelStatus,
      modelConfigured
        ? `model ${provider}/${modelId}; gateway ${gateway ? "present" : "absent"}`
        : "no model provider/model configured",
      {
        provider,
        model: modelId,
        gateway,
        credential: renderSecret(profile.settings.controller_api_key),
      },
    ),
  );

  // 9. secrets — the report must not contain a material credential.
  const apiKey = input.env[profile.settings.controller_api_key.env];
  const serialized = JSON.stringify({ settings: settingView(profile), checks });
  const leaked = typeof apiKey === "string" && apiKey.length > 0 && serialized.includes(apiKey);
  const suspicious = checks.some((entry) => {
    const value = entry.params["credential"];
    return typeof value === "string" && looksLikeSecret(value);
  });
  checks.push(
    check(
      "secrets",
      leaked || suspicious ? "fail" : "pass",
      leaked
        ? "a credential value appears in the report"
        : suspicious
          ? "a credential-shaped value appears in the report"
          : "no credential value in the report; secrets are env-referenced and masked",
      { controller_api_key: renderSecret(profile.settings.controller_api_key) },
    ),
  );

  const failed = checks.some((entry) => entry.status === "fail");
  return Object.freeze({
    schema: DOCTOR_SCHEMA,
    mode: connected === null ? "static" : "connect",
    environment: input.environment,
    status: failed ? "failed" : "ok",
    product_version: input.productVersion,
    bundle_version: BUNDLE_VERSION,
    node_version: nodeVersion,
    settings: settingView(profile),
    checks: Object.freeze(checks),
  });
}
