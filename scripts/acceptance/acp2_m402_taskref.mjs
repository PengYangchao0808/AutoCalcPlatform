#!/usr/bin/env node
/**
 * M4-02 same-TaskRef read leg: MCP (`acp_status`) and the Workbench client.
 *
 * Requirement: one TaskRef produced by a real Local submission is read by the
 * CLI, by MCP and by the Workbench client, and all three report the same
 * attempt/release/status.  This driver owns the last two readers:
 *
 *   - MCP: the real ``acp mcp serve`` child over stdio, driven through the real
 *     ``@rph/bridge`` (the same client the DSH harness uses).  The status
 *     operation is ``acp_status``; nothing here invents a platform fact.
 *   - Workbench: the *real* frontend modules
 *     (``js/client/resource-store.js`` + ``js/client/job-detail-query.js``) run
 *     in Node with a real HTTP ``fetch`` adapter against the live Controller —
 *     the same technique as the M3-05b live layer.  No browser is started.
 *
 * Usage: node acp2_m402_taskref.mjs --config <json> --out <json>
 * Exit codes: 0 both readers ran, 1 a reader failed, 2 a prerequisite is missing.
 */

import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import vm from "node:vm";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO = resolve(HERE, "..", "..");
const DSH_DIST = join(REPO, "rph", "packages", "dsh-bundle", "dist");
const BRIDGE_DIST = join(REPO, "rph", "packages", "bridge", "dist", "index.js");

const EXIT_OK = 0;
const EXIT_FAIL = 1;
const EXIT_PREREQ = 2;

function parseArgs(argv) {
  const args = { config: null, out: null };
  for (let index = 0; index < argv.length; index += 1) {
    const key = argv[index];
    if (key === "--config" || key === "--out") {
      const value = argv[index + 1];
      if (value === undefined || value.startsWith("--")) throw new Error(`${key} requires a value`);
      args[key.slice(2)] = value;
      index += 1;
    } else {
      throw new Error(`unknown argument ${key}`);
    }
  }
  if (args.config === null || args.out === null) throw new Error("--config and --out are required");
  return args;
}

function dataOf(result) {
  const data = result.structuredContent?.["data"];
  return typeof data === "object" && data !== null && !Array.isArray(data) ? data : null;
}

/** The Workbench client modules, loaded exactly as the page loads them. */
async function readWithWorkbench(config) {
  const loaded = [];
  global.window = global;
  for (const relative of config.workbench_modules) {
    const code = readFileSync(join(config.repo_root, relative), "utf8");
    vm.runInThisContext(code, { filename: relative });
    loaded.push(relative);
  }
  if (typeof global.ACPClient?.jobDetail?.fetchDetail !== "function") {
    return { ok: false, error: "ACPClient.jobDetail is not installed by the loaded modules" };
  }
  const base = String(config.controller_url).replace(/\/$/, "");
  const v1 = async (suffix, options) => {
    const response = await fetch(base + "/api/v1" + suffix, options);
    const text = await response.text();
    const body = text === "" ? null : JSON.parse(text);
    if (!response.ok) {
      const error = new Error(`HTTP ${response.status}`);
      error.status = response.status;
      error.body = body;
      throw error;
    }
    return body;
  };
  global.ACPClient.jobDetail.init({ v1 });
  const detail = await global.ACPClient.jobDetail.fetchDetail(String(config.task_id));
  const job = detail?.job ?? detail ?? null;
  const projection = detail?.projection ?? job?.projection ?? null;
  return {
    ok: true,
    modules: loaded,
    raw_kind: detail === null ? "null" : typeof detail,
    task_id: job?.id ?? job?.job_id ?? null,
    status: job?.status ?? null,
    attempt: detail?.attempt ?? job?.attempt ?? projection?.attempt ?? null,
    release_id:
      projection?.release_id ?? detail?.release?.release_id ?? job?.release_id ?? null,
    legacy: Boolean(detail?.legacy),
    keys: detail === null ? [] : Object.keys(detail).sort(),
  };
}

async function readWithMcp(config) {
  const bridgeModule = await import(pathToFileURL(BRIDGE_DIST).href);
  const sessionModule = await import(
    pathToFileURL(join(DSH_DIST, "src", "platform-session.js")).href
  );
  const launchModule = await import(
    pathToFileURL(join(DSH_DIST, "src", "mcp-stdio-launch.js")).href
  );
  const { Bridge } = bridgeModule;
  const { openMcpSession } = sessionModule;
  const { launchMcpStdio } = launchModule;
  const launched = launchMcpStdio(config.launch);
  let session = null;
  try {
    const init = await launched.client.initialize();
    session = await openMcpSession(launched.client, {
      mode: "product",
      projectId: config.project_id,
      client: { name: "acp2-m4-02-taskref", version: "m4-02" },
    });
    const bridge = new Bridge(session.client);
    const result = await bridge.execute({
      operation: "status",
      task: String(config.task_id),
      attempt: Number(config.attempt),
    });
    const data = dataOf(result) ?? {};
    const normalized = result.task_ref_normalized ?? data["task_ref"] ?? {};
    return {
      ok: !result.isError,
      protocol_version: String(init["protocolVersion"] ?? ""),
      tools: [...session.toolNames].sort(),
      tool: "acp_status",
      is_error: Boolean(result.isError),
      task_id: String(normalized.task_id ?? data["task_id"] ?? data["job_id"] ?? ""),
      status: String(data["status"] ?? ""),
      attempt: Number(normalized.attempt ?? data["attempt"] ?? 0),
      release_id: String(normalized.release?.release_id ?? data["release_id"] ?? ""),
      release_digest: String(
        normalized.release?.release_digest ?? data["release_digest"] ?? "",
      ),
      submission_id: String(data["submission_id"] ?? ""),
      task_ref: normalized,
      status_projection_keys: Object.keys(data).sort(),
      error: result.isError ? result.structuredContent?.["error"] ?? null : null,
    };
  } finally {
    try {
      await launched.close();
    } catch {
      /* the child is best-effort closed */
    }
  }
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const config = JSON.parse(readFileSync(args.config, "utf8"));
  const result = { schema: "acp2.m4-02.taskref.v1", status: "error", mcp: null, workbench: null, errors: [] };
  try {
    result.mcp = await readWithMcp(config);
  } catch (error) {
    result.errors.push(`mcp: ${String(error.message ?? error)}`);
  }
  try {
    result.workbench = await readWithWorkbench(config);
  } catch (error) {
    result.errors.push(`workbench: ${String(error.message ?? error)}`);
  }
  if (result.errors.length > 0) {
    writeFileSync(args.out, JSON.stringify(result, null, 2) + "\n");
    return EXIT_FAIL;
  }
  result.status = "ok";
  writeFileSync(args.out, JSON.stringify(result, null, 2) + "\n");
  return EXIT_OK;
}

main()
  .then((code) => process.exit(code))
  .catch((error) => {
    process.stderr.write(`${String(error.stack ?? error)}\n`);
    process.exit(EXIT_FAIL);
  });
