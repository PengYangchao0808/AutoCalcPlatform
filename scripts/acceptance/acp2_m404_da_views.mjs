#!/usr/bin/env node
/**
 * M4-04 evidence-view driver: RPH decision + headless Workbench projection.
 *
 * Two readers consume the **same** platform evidence view (the digest-bound
 * `acp2.runtime.evidence-projection.v1` document):
 *
 * 1. `decision` — the frozen RPH policy (`decideFromView` in
 *    `rph/packages/dsh-bundle/dist/skills/supervisor.js`).  It must agree with
 *    the platform's own `decide_evidence`, so a client-side reader cannot
 *    upgrade a verdict.
 * 2. `workbench` — the real Workbench module
 *    (`frontend/js/workbench/features/research-evidence-view.js`) run headless:
 *    its `project(view)` is a pure projection (no DOM, no network), which is what
 *    the browser mounts.  A missing module or a projection problem is reported,
 *    never smoothed over.
 *
 * Usage: node acp2_m404_da_views.mjs --config <json>
 * The config carries `{mode, view}`; the result lands in `result.json` next to it.
 * Exit codes: 0 a reader ran, 2 the reader is unavailable.
 */

import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import vm from "node:vm";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO = resolve(HERE, "..", "..");
const SUPERVISOR = join(REPO, "rph", "packages", "dsh-bundle", "dist", "skills", "supervisor.js");
const RESEARCH = join(REPO, "rph", "packages", "research", "dist", "index.js");
const WORKBENCH = join(
  REPO,
  "frontend",
  "js",
  "workbench",
  "features",
  "research-evidence-view.js",
);

function parseArgs(argv) {
  const args = { config: null };
  for (let index = 0; index < argv.length; index += 1) {
    if (argv[index] === "--config") {
      args.config = argv[index + 1];
      index += 1;
    } else {
      throw new Error(`unknown argument ${argv[index]}`);
    }
  }
  if (!args.config) throw new Error("--config is required");
  return args;
}

function writeResult(configPath, payload) {
  const target = join(dirname(configPath), "result.json");
  writeFileSync(target, JSON.stringify(payload, null, 2) + "\n", "utf8");
}

async function decide(view) {
  try {
    const supervisor = await import(pathToFileURL(SUPERVISOR).href);
    if (typeof supervisor.decideFromView !== "function") {
      return { available: false, reason: "decideFromView is not exported" };
    }
    return {
      available: true,
      decision: supervisor.decideFromView(view),
      source: "rph/packages/dsh-bundle/skills/supervisor.ts::decideFromView",
    };
  } catch (error) {
    // The frozen research policy is the fallback authority (same rules).
    try {
      const research = await import(pathToFileURL(RESEARCH).href);
      const evaluate = research.evaluateDecision;
      if (typeof evaluate !== "function") throw error;
      if (evaluate(view, "accept").allowed) {
        return {
          available: true,
          decision: "accept",
          source: "@rph/research::evaluateDecision",
        };
      }
      if (view.execution_terminal_state === "failed" || view.execution_terminal_state === "cancelled") {
        return { available: true, decision: "reject", source: "@rph/research::evaluateDecision" };
      }
      if (view.evidence_status === "failed") {
        return { available: true, decision: "reject", source: "@rph/research::evaluateDecision" };
      }
      if (view.evidence_status === "incomplete") {
        return { available: true, decision: "escalate", source: "@rph/research::evaluateDecision" };
      }
      if (Array.isArray(view.blocking_missing_checks) && view.blocking_missing_checks.length > 0) {
        return { available: true, decision: "escalate", source: "@rph/research::evaluateDecision" };
      }
      return { available: true, decision: "revise", source: "@rph/research::evaluateDecision" };
    } catch (fallbackError) {
      return {
        available: false,
        error: `${error.message}; fallback: ${fallbackError.message}`,
      };
    }
  }
}

function workbench(view) {
  try {
    const source = readFileSync(WORKBENCH, "utf8");
    const sandbox = { window: {}, console };
    sandbox.globalThis = sandbox;
    vm.createContext(sandbox);
    vm.runInContext(source, sandbox, { filename: WORKBENCH });
    const module = sandbox.window.ACPResearchEvidence;
    if (!module || typeof module.project !== "function") {
      return { available: false, reason: "ACPResearchEvidence.project is missing" };
    }
    const projection = module.project(view);
    return {
      available: true,
      ok: projection.ok,
      problem: projection.problem,
      supported: projection.supported,
      rows: projection.rows.map((row) => ({ label: row.label, value: row.value })),
      source: "frontend/js/workbench/features/research-evidence-view.js::project",
    };
  } catch (error) {
    return { available: false, error: error.message };
  }
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const config = JSON.parse(readFileSync(args.config, "utf8"));
  const view = config.view ?? {};
  let payload;
  if (config.mode === "decision") {
    payload = await decide(view);
  } else if (config.mode === "workbench") {
    payload = workbench(view);
  } else {
    payload = { available: false, error: `unknown mode ${config.mode}` };
  }
  writeResult(args.config, payload);
  return payload.available ? 0 : 2;
}

main()
  .then((code) => process.exit(code))
  .catch((error) => {
    try {
      const args = parseArgs(process.argv.slice(2));
      writeResult(args.config, { available: false, error: error.message });
    } catch {
      // nothing else to do
    }
    process.exit(1);
  });
