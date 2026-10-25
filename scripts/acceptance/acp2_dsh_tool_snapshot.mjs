#!/usr/bin/env node
/**
 * Real DSH profile tool-registration snapshot for the C `dsh-discovery-live`
 * acceptance row (T-12 / C08).
 *
 * This is NOT a code scan and NOT a second MCP transport. It boots the frozen
 * DSH source profile exactly the way `dsh --profile <web|headless>` does
 * (`apps/cli/src/profile-boot.ts::runProfile`), applies the requested Cordis
 * patch overlays, and reads the live `ctx.tools` registry:
 *
 *   - `ctx.tools.schemas()` is the global (host) registry view;
 *   - for `web`, the default agent preset is mounted on a fresh scope with the
 *     public `agentPresets.mount` + `createScope` APIs and `schemas(scopeOf)`
 *     is read from that scope, which is the model-visible set for that preset.
 *
 * Tool origin is recorded from a transparent wrapper around
 * `ToolRuntime.prototype.register`: the first stack frame inside
 * `packages/**` (skipping `vendor/cordis`) is resolved to its nearest
 * package.json `name`. Presence therefore comes from real registration; the
 * origin is runtime attribution, not a static name table.
 *
 * Usage:
 *   node acp2_dsh_tool_snapshot.mjs --profile web --patch <yml> [--patch ...] \
 *        --out <file> [--launch <mcp-launch.json>] [--web-port 0]
 *
 * Exit codes: 0 snapshot written; 2 prerequisite/import problem; 4 boot error.
 */

import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { pathToFileURL } from "node:url";

const EXIT_OK = 0;
const EXIT_PREREQ = 2;
const EXIT_BOOT = 4;

function parseArgs(argv) {
  const args = {
    profile: null,
    patches: [],
    out: null,
    launch: null,
    webPort: "0",
    probeExternal: null,
  };
  for (let index = 0; index < argv.length; index += 1) {
    const key = argv[index];
    if (
      key === "--profile" ||
      key === "--out" ||
      key === "--launch" ||
      key === "--web-port" ||
      key === "--probe-external"
    ) {
      const value = argv[index + 1];
      if (value === undefined || value.startsWith("--")) {
        throw new Error(`${key} requires a value`);
      }
      if (key === "--profile") args.profile = value;
      else if (key === "--out") args.out = value;
      else if (key === "--launch") args.launch = value;
      else if (key === "--web-port") args.webPort = value;
      else args.probeExternal = value;
      index += 1;
    } else if (key === "--patch") {
      const value = argv[index + 1];
      if (value === undefined || value.startsWith("--")) {
        throw new Error("--patch requires a value");
      }
      args.patches.push(value);
      index += 1;
    } else {
      throw new Error(`unknown argument ${key}`);
    }
  }
  if (args.profile !== "web" && args.profile !== "headless") {
    throw new Error("--profile must be web or headless");
  }
  if (args.out === null) throw new Error("--out is required");
  return args;
}

function readJson(path) {
  return JSON.parse(readFileSync(path, "utf8"));
}

function packageOf(file, cache) {
  if (cache.has(file)) return cache.get(file);
  let dir = dirname(file);
  let name = "unknown";
  for (let depth = 0; depth < 16; depth += 1) {
    try {
      const document = readJson(join(dir, "package.json"));
      if (typeof document.name === "string") {
        name = document.name;
        break;
      }
    } catch {
      /* keep walking */
    }
    const parent = dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  cache.set(file, name);
  return name;
}

function registeringPackage(stack, cache) {
  for (const raw of stack.split("\n").slice(1)) {
    const match = raw.trim().match(/\(?((?:file:\/\/)?\/[^:)]+):\d+:\d+\)?/);
    if (match === null) continue;
    const file = match[1].replace(/^file:\/\//, "");
    if (!file.includes("/packages/")) continue;
    if (file.includes("/vendor/")) continue;
    if (file.includes("/core/tools/")) continue;
    return { package: packageOf(file, cache), file };
  }
  return { package: "unknown", file: null };
}

// Cordis declares `FiberState` as a `const enum`, which is erased from the
// published JS. The numeric values are stable across the frozen revision.
const FIBER_PENDING = 0;
const FIBER_FAILED = 3;

function entryOutcome(entry) {
  const fiber = entry.fiber;
  const state = fiber === undefined ? null : fiber.state;
  return {
    id: entry.options.id,
    name: entry.options.name,
    disabled: Boolean(entry.disabled),
    fiber_state: state,
    failed: state === FIBER_FAILED,
    pending: state === FIBER_PENDING,
  };
}

async function entryError(entry) {
  const fiber = entry.fiber;
  if (fiber === undefined) return null;
  try {
    if (fiber.state !== FIBER_FAILED) return null;
    await fiber.await();
    return null;
  } catch (error) {
    return {
      name: String(error?.name ?? "Error"),
      message: String(error?.message ?? error),
      stack: typeof error?.stack === "string" ? error.stack.split("\n").slice(0, 8) : [],
    };
  }
}

function toolRow(schema, origin, scopeKind, defaultAction) {
  const requiredAction = defaultAction?.required_action ?? null;
  return {
    name: schema.name,
    description: typeof schema.description === "string" ? schema.description : null,
    source_plugin: origin?.package ?? "unknown",
    source_file: origin?.file ?? null,
    scope: scopeKind,
    mcp_namespaced: schema.name.startsWith("mcp__acp__"),
    permission_class:
      requiredAction === null ? "dsh-native" : `mcp:${requiredAction}`,
    side_effects: defaultAction?.side_effects ?? null,
    parameter_keys:
      schema.parameters && typeof schema.parameters === "object"
        ? Object.keys(schema.parameters.properties ?? {}).sort()
        : [],
  };
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const dshSource = process.env.ACP2_DSH_SOURCE;
  const result = {
    schema: "acp2.c.dsh-discovery.tool-snapshot.v1",
    status: "error",
    profile: args.profile,
    source: "real_registration",
    booted_via: "apps/cli/src/profile-boot.ts::runProfile",
    patches: args.patches,
    web_url: null,
    model_visible_scope: args.profile === "web" ? "agent-preset" : "global",
    global_tools: [],
    model_visible_tools: [],
    presets: [],
    bound_preset: null,
    acp_entry: null,
    acp_entry_error: null,
    permission_policy: {},
    stderr_lines: [],
    launched_at: new Date().toISOString(),
  };

  const writeResult = (code) => {
    mkdirSync(dirname(args.out), { recursive: true });
    result.finished_at = new Date().toISOString();
    writeFileSync(args.out, JSON.stringify(result, null, 2) + "\n");
    process.exitCode = code;
  };

  if (!dshSource) {
    result.error = { name: "PrerequisiteError", message: "ACP2_DSH_SOURCE is not set" };
    writeResult(EXIT_PREREQ);
    return;
  }
  const dshHome = process.env.ACP2_DSH_HOME;
  if (!dshHome) {
    result.error = { name: "PrerequisiteError", message: "ACP2_DSH_HOME is not set" };
    writeResult(EXIT_PREREQ);
    return;
  }

  let modules;
  try {
    // tsx resolves the workspace's `@deepseek-ai/*` specifiers through
    // tsconfig.base.json `paths` to these SRC modules. Importing the same files
    // keeps one module instance (and one scope symbol / one ToolRuntime class)
    // shared with the plugins the profile boots.
    const srcModule = (relative) => pathToFileURL(join(dshSource, relative)).href;
    const [appBoot, scope, tools, profileBoot] = await Promise.all([
      import(srcModule("packages/boot/app-boot/src/index.ts")),
      import(srcModule("packages/core/scope/src/index.ts")),
      import(srcModule("packages/core/tools/src/index.ts")),
      import(srcModule("apps/cli/src/profile-boot.ts")),
    ]);
    modules = {
      loadLayeredEnv: appBoot.loadLayeredEnv,
      createScope: scope.createScope,
      scopeOf: scope.scopeOf,
      ToolRuntime: tools.ToolRuntime,
      runProfile: profileBoot.runProfile,
    };
  } catch (error) {
    result.error = {
      name: String(error?.name ?? "PrerequisiteError"),
      message: `cannot load the frozen DSH profile boot: ${String(error?.message ?? error)}`,
    };
    writeResult(EXIT_PREREQ);
    return;
  }

  const launchActions = new Map();
  if (args.launch !== null) {
    try {
      const launch = readJson(args.launch);
      for (const tool of launch.tools ?? []) {
        if (typeof tool?.name === "string") {
          launchActions.set(tool.name, {
            required_action: tool.required_action ?? null,
            side_effects: tool.side_effects ?? null,
            version: tool.version ?? null,
          });
        }
      }
      result.launch = {
        path: args.launch,
        server: launch.server ?? null,
        protocol_version: launch.protocol_version ?? null,
        tool_names: [...launchActions.keys()].sort(),
      };
    } catch (error) {
      result.error = {
        name: "PrerequisiteError",
        message: `cannot read launch descriptor: ${String(error?.message ?? error)}`,
      };
      writeResult(EXIT_PREREQ);
      return;
    }
  }

  const origins = new Map();
  const packageCache = new Map();
  const originalRegister = modules.ToolRuntime.prototype.register;
  modules.ToolRuntime.prototype.register = function register(definition) {
    try {
      origins.set(definition.name, registeringPackage(new Error().stack ?? "", packageCache));
    } catch {
      origins.set(definition.name, { package: "unknown", file: null });
    }
    return originalRegister.call(this, definition);
  };

  const stderrLines = [];
  const stdoutLines = [];
  const originalStderrWrite = process.stderr.write.bind(process.stderr);
  const originalStdoutWrite = process.stdout.write.bind(process.stdout);
  process.stderr.write = (chunk, ...rest) => {
    const text = typeof chunk === "string" ? chunk : Buffer.from(chunk).toString("utf8");
    stderrLines.push(text);
    return originalStderrWrite(chunk, ...rest);
  };
  process.stdout.write = (chunk, ...rest) => {
    const text = typeof chunk === "string" ? chunk : Buffer.from(chunk).toString("utf8");
    stdoutLines.push(text);
    return originalStdoutWrite(chunk, ...rest);
  };

  const innerArgs =
    args.profile === "web"
      ? ["--host", "127.0.0.1", "--port", args.webPort, "--no-open"]
      : ["--json", "acceptance-tool-snapshot"];
  result.inner_argv = innerArgs;

  let ctx;
  let shutdown;
  try {
    ({ ctx, shutdown } = await modules.runProfile({
      environment: modules.loadLayeredEnv("dsh"),
      profile: args.profile,
      patchFiles: args.patches,
      args: innerArgs,
    }));
  } catch (error) {
    result.error = {
      name: String(error?.name ?? "Error"),
      message: String(error?.message ?? error),
      stack: typeof error?.stack === "string" ? error.stack.split("\n").slice(0, 12) : [],
    };
    process.stdout.write = originalStdoutWrite;
    process.stderr.write = originalStderrWrite;
    writeResult(EXIT_BOOT);
    return;
  }

  try {
    const loader = ctx.get("loader");
    if (loader !== undefined) {
      for (const entry of loader.entries()) {
        if (entry.options.id === "acp-mcp-acceptance") {
          result.acp_entry = entryOutcome(entry);
          result.acp_entry_error = await entryError(entry);
        }
      }
    }

    const registry = ctx.get("tools");
    const globalSchemas = registry === undefined ? [] : registry.schemas();
    result.global_tools = globalSchemas.map((schema) =>
      toolRow(schema, origins.get(schema.name), "global", launchActions.get(schema.name)),
    );

    let modelSchemas = globalSchemas;
    let scopeKind = "global";
    if (args.profile === "web") {
      const presets = ctx.get("agentPresets");
      if (presets === undefined) throw new Error("agentPresets service is not mounted");
      result.presets = await presets.list();
      const scope = modules.createScope(ctx, {});
      try {
        result.bound_preset = await presets.mount(scope.ctx);
        const key = modules.scopeOf(scope.ctx);
        modelSchemas = registry === undefined ? [] : registry.schemas(key);
        scopeKind = `agent-preset:${result.bound_preset?.id ?? "unknown"}`;
      } finally {
        await scope.dispose();
      }
    }
    result.model_visible_tools = modelSchemas.map((schema) =>
      toolRow(schema, origins.get(schema.name), scopeKind, launchActions.get(schema.name)),
    );

    const approval = ctx.get("approval");
    const sandbox = ctx.get("sandboxPolicy");
    result.permission_policy = {
      approval_policy: approval?.config?.policy ?? null,
      sandbox_mode: sandbox?.config?.mode ?? process.env.DSH_PERMISSION_MODE ?? null,
      note: "DSH applies approval/sandbox policy per session; the MCP allowlist is enforced server-side.",
    };

    if (args.profile === "web") {
      const port = Number(args.webPort);
      const base = `http://127.0.0.1:${String(port)}/`;
      const probe = async (url) => {
        try {
          const response = await fetch(url, {
            redirect: "manual",
            signal: AbortSignal.timeout(5000),
          });
          return { reachable: true, status: response.status };
        } catch (error) {
          return {
            reachable: false,
            error: String(error?.cause?.code ?? error?.message ?? error),
          };
        }
      };
      const loopback = await probe(base);
      const external =
        args.probeExternal === null
          ? { probed: false }
          : { probed: true, address: args.probeExternal, ...(await probe(`http://${args.probeExternal}:${String(port)}/`)) };
      result.web_url = {
        href: base,
        host: "127.0.0.1",
        port,
        requested_argv: innerArgs,
        loopback_only_requested: innerArgs.includes("127.0.0.1"),
        production_8765: port === 8765,
        loopback,
        external,
        console_url_line: stdoutLines
          .join("")
          .split("\n")
          .find((line) => line.startsWith("dsh web:")) ?? null,
      };
    }

    result.status = "ok";
    writeResult(EXIT_OK);
  } catch (error) {
    result.error = {
      name: String(error?.name ?? "Error"),
      message: String(error?.message ?? error),
      stack: typeof error?.stack === "string" ? error.stack.split("\n").slice(0, 12) : [],
    };
    writeResult(EXIT_BOOT);
  } finally {
    process.stdout.write = originalStdoutWrite;
    process.stderr.write = originalStderrWrite;
    result.stderr_lines = stderrLines.join("").split("\n").filter((line) => line.length > 0).slice(-40);
    if (shutdown !== undefined) {
      try {
        await shutdown.shutdown(0);
      } catch {
        /* recorded through the snapshot's stderr_lines */
      }
    }
    writeResult(process.exitCode ?? EXIT_OK);
  }
}

main().catch((error) => {
  process.stderr.write(`acp2_dsh_tool_snapshot: ${String(error?.stack ?? error)}\n`);
  process.exitCode = EXIT_PREREQ;
});
