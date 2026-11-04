/**
 * Portable DSH host launch profile — pure builder, no host literals.
 *
 * `profiles/web.cordis.yml` stays a frozen acceptance record; this suite proves
 * the *replacement* is parameterised and deterministic:
 *
 *   - the same input yields a deep-equal profile and byte-identical patch text;
 *   - the Controller is an absolute http(s) URL without credentials/query/fragment;
 *   - the argv is exactly the documented launch vector, in order;
 *   - env keeps the fixed order, replaces a caller duplicate in place and sorts
 *     the extra names;
 *   - quoting round-trips a single quote through the renderer;
 *   - the rendered document has the Cordis shape and no acceptance host literal;
 *   - a credential (`userinfo`, `TOKEN=`) is refused by the portability guard;
 *   - `src/host-profile.ts` keeps no forbidden boundary token.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import {
  DEFAULT_SERVER_NAME,
  DEFAULT_TOOL_CALL_TIMEOUT_MS,
  HOST_PROFILE_PATCH_SCHEMA,
  HOST_PROFILE_SCHEMA,
  HostProfileError,
  assertPortableHostProfile,
  buildHostProfile,
  renderCordisPatch,
  type HostProfile,
  type HostProfileInput,
} from "@rph/dsh-bundle";

const CONTROLLER = "http://127.0.0.1:8765";

function baseInput(): HostProfileInput {
  return {
    controller: CONTROLLER,
    pythonInterpreter: "/opt/acp/venv/bin/python",
    cwd: "/srv/acp/src",
    runRoot: "/srv/acp/runs",
  };
}

/** Run `run` and return the `HostProfileError` it threw (or fail loudly). */
function thrownBy(run: () => unknown): HostProfileError {
  try {
    run();
  } catch (error: unknown) {
    if (error instanceof HostProfileError) {
      return error;
    }
    throw new Error(`expected HostProfileError, got ${String(error)}`);
  }
  throw new Error("expected HostProfileError, nothing was thrown");
}

function envNames(profile: HostProfile): string[] {
  return profile.env.map((entry) => entry.name);
}

function envValue(profile: HostProfile, name: string): string | undefined {
  return profile.env.find((entry) => entry.name === name)?.value;
}

/** Unquote one emitted single-quoted YAML scalar (test-local, no dependency). */
function unquoteSingle(scalar: string): string {
  assert.ok(scalar.startsWith("'"), `not a single-quoted scalar: ${scalar}`);
  assert.ok(scalar.endsWith("'"), `not a single-quoted scalar: ${scalar}`);
  return scalar.slice(1, -1).replaceAll("''", "'");
}

/** Read the scalar that follows `prefix` in the first line that starts with it. */
function scalarAfter(text: string, prefix: string): string {
  const line = text.split("\n").find((entry) => entry.trimStart().startsWith(prefix));
  if (line === undefined) {
    throw new Error(`no rendered line starts with ${prefix}`);
  }
  const separator = line.indexOf(": ");
  if (separator < 0) {
    throw new Error(`rendered line has no value: ${line}`);
  }
  return line.slice(separator + 2).trim();
}

// ---------------------------------------------------------------------------
// 1. Determinism and purity
// ---------------------------------------------------------------------------

test("build and render are deterministic and immutable", () => {
  const first = buildHostProfile(baseInput());
  const second = buildHostProfile(baseInput());

  assert.deepEqual(first, second);
  assert.equal(JSON.stringify(first), JSON.stringify(second));
  assert.equal(renderCordisPatch(first), renderCordisPatch(second));

  assert.equal(first.schema, HOST_PROFILE_SCHEMA);
  assert.equal(first.serverName, DEFAULT_SERVER_NAME);
  assert.equal(first.transport, "stdio");
  assert.equal(first.toolCallTimeoutMs, DEFAULT_TOOL_CALL_TIMEOUT_MS);
  assert.equal(first.command, "/opt/acp/venv/bin/python");
  assert.equal(first.cwd, "/srv/acp/src");
  assert.equal(Object.isFrozen(first), true);
  assert.equal(Object.isFrozen(first.args), true);
  assert.equal(Object.isFrozen(first.env), true);
});

test("build never mutates its input", () => {
  const input = baseInput();
  input.env = { ZED: "1", ALPHA: "2" };
  const before = JSON.stringify(input);
  buildHostProfile(input);
  assert.equal(JSON.stringify(input), before);
});

// ---------------------------------------------------------------------------
// 2. Controller validation
// ---------------------------------------------------------------------------

test("controller must be an absolute http(s) URL without credentials, query or fragment", () => {
  for (const controller of [
    "ftp://127.0.0.1:8765",
    "http://user:pw@host:8765",
    "http://host:8765/path?q=1",
    "http://host:8765/#f",
    "http://host:8765#f",
    "http://",
    "",
    "   ",
  ]) {
    const error = thrownBy(() => buildHostProfile({ ...baseInput(), controller }));
    assert.equal(error.code, "invalid_controller", controller);
    assert.equal(error.field, "controller", controller);
  }
});

test("controller is accepted and a trailing slash is normalised away", () => {
  const bare = buildHostProfile({ ...baseInput(), controller: "http://127.0.0.1:8765" });
  assert.equal(bare.args[7], "http://127.0.0.1:8765");
  assert.equal(envValue(bare, "ACP_CONTROLLER_URL"), "http://127.0.0.1:8765");

  const slashed = buildHostProfile({ ...baseInput(), controller: "http://127.0.0.1:8765/" });
  assert.deepEqual(slashed, bare);

  const secure = buildHostProfile({
    ...baseInput(),
    controller: "https://controller.example.org/base/",
  });
  assert.equal(secure.args[7], "https://controller.example.org/base");
});

// ---------------------------------------------------------------------------
// 3. The argv is the documented vector, in order
// ---------------------------------------------------------------------------

test("args are exactly the documented launch vector, in order", () => {
  const profile = buildHostProfile(baseInput());
  assert.deepEqual([...profile.args], [
    "-m",
    "acp",
    "mcp",
    "serve",
    "--transport",
    "stdio",
    "--controller",
    CONTROLLER,
    "--log-level",
    "INFO",
  ]);
  assert.equal(profile.args.length, 10);

  const custom = buildHostProfile({ ...baseInput(), logLevel: "DEBUG" });
  assert.deepEqual([...custom.args], [
    "-m",
    "acp",
    "mcp",
    "serve",
    "--transport",
    "stdio",
    "--controller",
    CONTROLLER,
    "--log-level",
    "DEBUG",
  ]);
});

// ---------------------------------------------------------------------------
// 4. Environment ordering, replacement in place, extra-name sorting
// ---------------------------------------------------------------------------

test("env keeps the fixed order and only carries the optional pairs when given", () => {
  const bare = buildHostProfile(baseInput());
  assert.deepEqual(envNames(bare), [
    "ACP_RUN_ROOT",
    "ACP_CONTROLLER_URL",
    "ACP2_MCP_LOG_LEVEL",
  ]);
  assert.equal(envValue(bare, "ACP_RUN_ROOT"), "/srv/acp/runs");
  assert.equal(envValue(bare, "ACP2_MCP_LOG_LEVEL"), "INFO");

  const full = buildHostProfile({
    ...baseInput(),
    toolsRoot: "/srv/acp/tools",
    configPath: "/srv/acp/cccp.yaml",
  });
  assert.deepEqual(envNames(full), [
    "ACP_RUN_ROOT",
    "ACP_CONTROLLER_URL",
    "ACP2_MCP_LOG_LEVEL",
    "ACP2_TOOLS",
    "ACP_CONFIG",
  ]);
  assert.equal(envValue(full, "ACP2_TOOLS"), "/srv/acp/tools");
  assert.equal(envValue(full, "ACP_CONFIG"), "/srv/acp/cccp.yaml");
});

test("a caller entry replaces a fixed pair in place and is never appended twice", () => {
  const profile = buildHostProfile({
    ...baseInput(),
    env: { ZZZ: "last", ACP_RUN_ROOT: "/srv/acp/other-runs", AAA: "first" },
  });
  assert.deepEqual(envNames(profile), [
    "ACP_RUN_ROOT",
    "ACP_CONTROLLER_URL",
    "ACP2_MCP_LOG_LEVEL",
    "AAA",
    "ZZZ",
  ]);
  const runRootPairs = profile.env.filter((entry) => entry.name === "ACP_RUN_ROOT");
  assert.equal(runRootPairs.length, 1);
  assert.equal(runRootPairs[0]?.value, "/srv/acp/other-runs");

  const names = envNames(profile);
  assert.equal(new Set(names).size, names.length);
});

test("extra env names are sorted by name", () => {
  const profile = buildHostProfile({
    ...baseInput(),
    env: { ZZ_LAST: "1", MM_MIDDLE: "2", AA_FIRST: "3", BB_SECOND: "4" },
  });
  assert.deepEqual(envNames(profile).slice(3), ["AA_FIRST", "BB_SECOND", "MM_MIDDLE", "ZZ_LAST"]);
});

test("custom env variable names are honoured", () => {
  const profile = buildHostProfile({
    ...baseInput(),
    runRootEnv: "RPH_RUN_ROOT",
    logLevelEnv: "RPH_LOG_LEVEL",
    env: { RPH_RUN_ROOT: "/srv/replaced" },
  });
  assert.deepEqual(envNames(profile), ["RPH_RUN_ROOT", "ACP_CONTROLLER_URL", "RPH_LOG_LEVEL"]);
  assert.equal(profile.env[0]?.value, "/srv/replaced");
});

test("colliding env names are a duplicate_env", () => {
  const callerCollision = thrownBy(() =>
    buildHostProfile({ ...baseInput(), env: { PATH: "/a", Path: "/b" } }));
  assert.equal(callerCollision.code, "duplicate_env");
  assert.equal(callerCollision.field, "env.Path");

  const fixedCollision = thrownBy(() =>
    buildHostProfile({ ...baseInput(), runRootEnv: "ACP_CONTROLLER_URL" }));
  assert.equal(fixedCollision.code, "duplicate_env");
});

// ---------------------------------------------------------------------------
// 5. Input validation codes
// ---------------------------------------------------------------------------

test("path-ish values and scalars are refused with a stable code and field", () => {
  const base = baseInput();
  const cases: readonly [HostProfileInput, string, string][] = [
    [{ ...base, cwd: "   " }, "invalid_cwd", "cwd"],
    [{ ...base, cwd: "/srv/a\nb" }, "invalid_cwd", "cwd"],
    [{ ...base, cwd: "http://user:pw@host/x" }, "credential_leak", "cwd"],
    [{ ...base, runRoot: "" }, "invalid_run_root", "runRoot"],
    [{ ...base, runRoot: "/srv/x\u0000y" }, "invalid_run_root", "runRoot"],
    [{ ...base, pythonInterpreter: "python\n3" }, "invalid_interpreter", "pythonInterpreter"],
    [{ ...base, pythonInterpreter: " " }, "invalid_interpreter", "pythonInterpreter"],
    [{ ...base, env: { "9LIVES": "x" } }, "invalid_input", "env.9LIVES"],
    [{ ...base, serverName: "bad name" }, "invalid_input", "serverName"],
    [{ ...base, logLevel: "9-INFO" }, "invalid_input", "logLevel"],
    [{ ...base, toolCallTimeoutMs: 0 }, "invalid_input", "toolCallTimeoutMs"],
    [{ ...base, toolCallTimeoutMs: 1.5 }, "invalid_input", "toolCallTimeoutMs"],
  ];
  for (const [input, code, field] of cases) {
    const error = thrownBy(() => buildHostProfile(input));
    assert.equal(error.code, code, `${code}/${field}`);
    assert.equal(error.field, field, `${code}/${field}`);
  }
});

// ---------------------------------------------------------------------------
// 6. Renderer: quoting, structure and no frozen acceptance literal
// ---------------------------------------------------------------------------

test("a single quote round-trips through the renderer", () => {
  const configPath = "/srv/acp/it's/cccp.yaml";
  const profile = buildHostProfile({ ...baseInput(), configPath });
  const text = renderCordisPatch(profile);

  assert.equal(text.includes("it''s"), true, "the emitted scalar must double the quote");
  const scalar = scalarAfter(text, "'ACP_CONFIG':");
  assert.equal(unquoteSingle(scalar), configPath);
});

test("the rendered document is a Cordis patch with one insert row", () => {
  const profile = buildHostProfile({ ...baseInput(), env: { PATH: "/usr/bin:/bin" } });
  const text = renderCordisPatch(profile);

  assert.equal(text.startsWith("#"), true);
  assert.equal(text.includes(HOST_PROFILE_PATCH_SCHEMA), true);
  assert.equal(text.includes(HOST_PROFILE_SCHEMA), true);
  assert.equal(text.includes("--patch"), true);
  assert.equal(text.includes("\n- insert:\n"), true);
  assert.equal(text.includes("      name: '@deepseek-ai/dsh-mcp-client'"), true);
  assert.equal(text.split("\n    - id: ").length - 1, 1);
  assert.equal(text.includes(`    - id: 'acp-mcp-${DEFAULT_SERVER_NAME}'`), true);
  assert.equal(text.includes("        transport: 'stdio'"), true);
  assert.equal(text.includes(`        toolCallTimeoutMs: ${String(DEFAULT_TOOL_CALL_TIMEOUT_MS)}`), true);
  assert.equal(text.includes("          - '--controller'"), true);

  assert.equal(text.endsWith("\n"), true);
  assert.equal(text.endsWith("\n\n"), false);
  assert.equal(text.includes("\t"), false);
  for (const line of text.split("\n")) {
    assert.equal(/[ \t]+$/.test(line), false, `trailing whitespace: ${JSON.stringify(line)}`);
  }
});

test("the rendered text carries no frozen acceptance host literal", () => {
  const text = renderCordisPatch(
    buildHostProfile({ ...baseInput(), env: { PATH: "/usr/bin:/bin" } }),
  );
  for (const forbidden of ["/ACPServer", "18765", "acp2-development", "venvs/"]) {
    assert.equal(text.includes(forbidden), false, `rendered text contains ${forbidden}`);
  }
});

test("a single profile and a one-element list render identically", () => {
  const profile = buildHostProfile(baseInput());
  assert.equal(renderCordisPatch(profile), renderCordisPatch([profile]));
});

test("extraRows are appended after the insert row, deterministically", () => {
  const profile = buildHostProfile(baseInput());
  const extraRows = [
    { id: "preset-standard", disabled: true },
    { id: "agent-preset-registry", config: { default: "rph-research" } },
  ] as const;
  const first = renderCordisPatch(profile, { extraRows });
  const second = renderCordisPatch(profile, { extraRows });

  assert.equal(first, second);
  assert.equal(
    first.includes(`- ${JSON.stringify(extraRows[0])}\n- ${JSON.stringify(extraRows[1])}\n`),
    true,
  );
  assert.ok(
    first.indexOf(`- ${JSON.stringify(extraRows[0])}`) >
      first.indexOf("        toolCallTimeoutMs:"),
  );
  assert.equal(first.endsWith("\n"), true);
  assert.equal(first.endsWith("\n\n"), false);
});

// ---------------------------------------------------------------------------
// 7. Portability guard
// ---------------------------------------------------------------------------

test("assertPortableHostProfile accepts a clean profile", () => {
  assertPortableHostProfile(buildHostProfile({ ...baseInput(), env: { PATH: "/usr/bin" } }));
});

test("assertPortableHostProfile refuses userinfo and secret-shaped values", () => {
  const userinfo = buildHostProfile({
    ...baseInput(),
    env: { ACP_ENDPOINT: "https://user:pw@controller.example.org" },
  });
  const userinfoError = thrownBy(() => assertPortableHostProfile(userinfo));
  assert.equal(userinfoError.code, "credential_leak");
  assert.equal(userinfoError.field, "env.ACP_ENDPOINT");

  for (const value of ["TOKEN=abc123", "API_KEY=sk-live", "MY_PASSWORD=hunter2"]) {
    const profile = buildHostProfile({ ...baseInput(), env: { CREDENTIAL: value } });
    const error = thrownBy(() => assertPortableHostProfile(profile));
    assert.equal(error.code, "credential_leak", value);
    assert.equal(error.field, "env.CREDENTIAL", value);
  }
});

// ---------------------------------------------------------------------------
// 8. Boundary guard: the builder stays a pure data/string module
// ---------------------------------------------------------------------------

test("src/host-profile.ts contains no forbidden boundary token", () => {
  const source = readFileSync(
    fileURLToPath(new URL("../../packages/dsh-bundle/src/host-profile.ts", import.meta.url)),
    "utf8",
  );
  for (const forbidden of [
    "fetch(",
    "child_process",
    "node:http",
    "new WebSocket(",
    "createServer(",
    "listen(",
    '"ssh2"',
  ]) {
    assert.equal(
      source.includes(forbidden),
      false,
      `host-profile.ts must not contain ${forbidden}`,
    );
  }
});
