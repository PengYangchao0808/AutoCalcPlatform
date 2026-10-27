/**
 * M3-02 / G17 — portable runtime profiles.
 *
 * The profile is the only place that may name a parameter; every host value
 * (interpreter, cwd, run root, config, artifact path, model, writer, key) comes
 * from the environment. Asserted per item, so removing one portability rule
 * fails a named test instead of a serialized-blob comparison.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  RUNTIME_ENVIRONMENTS,
  RUNTIME_PROFILE_MATRIX,
  RUNTIME_SETTING_NAMES,
  RuntimeProfileError,
  isRuntimeEnvironment,
  looksLikeSecret,
  renderSecret,
  resolveRuntimeProfile,
} from "@rph/dsh-bundle";

import { TEST_OPTIONS } from "./harness.js";

const HOST_PATH_SETTINGS = [
  "working_directory",
  "run_root",
  "config_path",
  "artifact_evidence_path",
] as const;

function serialized(environment: (typeof RUNTIME_ENVIRONMENTS)[number]): string {
  return JSON.stringify(RUNTIME_PROFILE_MATRIX[environment]);
}

test("exactly three environments, each with every parameter", () => {
  assert.deepEqual([...RUNTIME_ENVIRONMENTS], ["dev", "acceptance", "release"]);
  for (const environment of RUNTIME_ENVIRONMENTS) {
    const spec = RUNTIME_PROFILE_MATRIX[environment];
    assert.equal(spec.environment, environment);
    assert.deepEqual(Object.keys(spec.settings).sort(), [...RUNTIME_SETTING_NAMES].sort());
  }
  assert.equal(isRuntimeEnvironment("acceptance"), true);
  assert.equal(isRuntimeEnvironment("prod"), false);
});

test("dev is the only environment that may run the labelled demo", () => {
  assert.equal(RUNTIME_PROFILE_MATRIX.dev.allow_demo, true);
  for (const environment of ["acceptance", "release"] as const) {
    assert.equal(RUNTIME_PROFILE_MATRIX[environment].allow_demo, false);
    assert.equal(RUNTIME_PROFILE_MATRIX[environment].require_model_gateway, true);
    assert.equal(RUNTIME_PROFILE_MATRIX[environment].require_research_writer, true);
    assert.equal(RUNTIME_PROFILE_MATRIX[environment].fail_closed_tool_surface, true);
  }
  assert.equal(RUNTIME_PROFILE_MATRIX.dev.fail_closed_tool_surface, false);
});

test("no profile carries an absolute interpreter path", () => {
  for (const environment of RUNTIME_ENVIRONMENTS) {
    const setting = RUNTIME_PROFILE_MATRIX[environment].settings.interpreter;
    const value = setting.default ?? "";
    assert.equal(value.includes("/"), false, `${environment} interpreter default is a path`);
    assert.equal(value.includes("\\"), false, `${environment} interpreter default is a path`);
    if (environment !== "dev") {
      // A product environment must be told its interpreter; there is no default.
      assert.equal(setting.default, undefined, `${environment} must not default the interpreter`);
      assert.equal(setting.required, true);
    }
  }
  assert.equal(RUNTIME_PROFILE_MATRIX.dev.settings.interpreter.default, "python3");
});

test("no profile carries a checkout cwd, run root, config or evidence path", () => {
  for (const environment of RUNTIME_ENVIRONMENTS) {
    for (const name of HOST_PATH_SETTINGS) {
      const setting = RUNTIME_PROFILE_MATRIX[environment].settings[name];
      assert.equal(setting.default, undefined, `${environment}.${name} carries a default`);
    }
  }
  for (const environment of ["acceptance", "release"] as const) {
    for (const name of HOST_PATH_SETTINGS) {
      assert.equal(
        RUNTIME_PROFILE_MATRIX[environment].settings[name].required,
        true,
        `${environment}.${name} must be required`,
      );
    }
  }
});

test("no profile carries a plaintext key", () => {
  for (const environment of RUNTIME_ENVIRONMENTS) {
    const setting = RUNTIME_PROFILE_MATRIX[environment].settings.controller_api_key;
    assert.equal(setting.secret, true);
    assert.equal(setting.default, undefined);
    assert.equal(setting.required, false);
  }
  for (const environment of RUNTIME_ENVIRONMENTS) {
    const text = serialized(environment);
    assert.equal(/sk-[A-Za-z0-9]{16,}/.test(text), false, `${environment} contains a key value`);
    assert.equal(/Bearer\s+\S+/.test(text), false, `${environment} contains a bearer token`);
  }
});

test("release has no defaults at all; acceptance defaults none of its parameters", () => {
  for (const name of RUNTIME_SETTING_NAMES) {
    assert.equal(
      RUNTIME_PROFILE_MATRIX.release.settings[name].default,
      undefined,
      `release.${name} must be supplied by the host`,
    );
  }
  assert.equal(RUNTIME_PROFILE_MATRIX.dev.settings.model_requests_budget.default, "8");
});

test("the serialized matrix names no acceptance host path or repository layout", () => {
  for (const environment of RUNTIME_ENVIRONMENTS) {
    const text = serialized(environment);
    for (const forbidden of [
      "/ACPServer",
      "acp2-runs",
      "acp2-development",
      "venvs/",
      "chdir",
    ]) {
      assert.equal(text.includes(forbidden), false, `${environment} contains ${forbidden}`);
    }
    assert.equal(/"[^"]*\/[A-Za-z0-9._-]+\/[A-Za-z0-9._-]+"/.test(text), false);
  }
});

test("resolution is environment-only and reports every missing required parameter", () => {
  const empty = resolveRuntimeProfile("acceptance", {});
  assert.equal(empty.get("interpreter"), null);
  assert.equal(empty.issues.length, empty.missing.length);
  assert.equal(empty.missing.includes("run_root"), true);
  assert.equal(empty.missing.includes("interpreter"), true);
  const issue = empty.issues.find((entry) => entry.setting === "run_root");
  assert.equal(issue?.env, "ACP_RUN_ROOT");
  assert.match(issue?.detail ?? "", /requires run_root/);

  const supplied = resolveRuntimeProfile("acceptance", {
    ACP2_CONTROLLER_URL: "http://127.0.0.1:18765",
    RPH_INTERPRETER: "python3.11",
    RPH_WORKING_DIRECTORY: "/srv/rph",
    ACP_RUN_ROOT: "/srv/run-root",
    ACP_CONFIG: "/srv/cccp.yaml",
    RPH_PROJECT: "proj-1",
    RPH_PERMISSION_SCOPE: "read",
    RPH_ARTIFACT_EVIDENCE_PATH: "/srv/artifacts",
    RPH_RESEARCH_WRITER: "research-store",
    RPH_MODEL_REQUESTS: "64",
  });
  assert.deepEqual(supplied.missing, []);
  assert.equal(supplied.get("interpreter"), "python3.11");
  assert.equal(supplied.settings.interpreter.source, "env");

  const dev = resolveRuntimeProfile("dev", {});
  assert.equal(dev.get("interpreter"), "python3");
  assert.equal(dev.settings.interpreter.source, "default");
  assert.deepEqual(dev.missing, []);

  assert.throws(
    () => resolveRuntimeProfile("prod" as "dev", {}),
    RuntimeProfileError,
  );
});

test("a secret renders masked and never leaks the value", () => {
  const profile = resolveRuntimeProfile("release", {
    RPH_CONTROLLER_API_KEY: "sk-livesecret0123456789abcdef",
  });
  const setting = profile.settings.controller_api_key;
  assert.equal(renderSecret(setting), "<redacted:env>");
  assert.equal(renderSecret(setting).includes("livesecret"), false);
  assert.equal(renderSecret(profile.settings.project_id), "<unset>");
  assert.equal(looksLikeSecret("sk-livesecret0123456789abcdef"), true);
  assert.equal(looksLikeSecret("RPH_CONTROLLER_API_KEY"), false);
  assert.equal(looksLikeSecret("read"), false);
  assert.equal(looksLikeSecret("sha256:" + "a".repeat(64)), false);
});

test("profile resolution is pure (no ambient environment read)", () => {
  const before = JSON.stringify(process.env.ACP_RUN_ROOT ?? "");
  const profile = resolveRuntimeProfile("dev", {});
  assert.equal(profile.get("run_root"), null);
  assert.equal(JSON.stringify(process.env.ACP_RUN_ROOT ?? ""), before);
});
