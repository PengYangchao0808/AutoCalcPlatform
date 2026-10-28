#!/usr/bin/env node
/**
 * Build the workspace, then run Node's built-in test runner over every
 * compiled `test/dist/**\/*.test.js` plus the hand-written
 * `test/**\/*.test.mjs` guards (which import build scripts directly).
 *
 * An empty test set is a failure (exit 1, "EMPTY TEST SET"), never a pass.
 */

import { spawnSync } from "node:child_process";
import { readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = fileURLToPath(new URL("..", import.meta.url));
const TSC = join(ROOT, "node_modules", "typescript", "bin", "tsc");

function walk(dir) {
  const out = [];
  let entries;
  try {
    entries = readdirSync(dir);
  } catch {
    return out;
  }
  for (const entry of entries) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) out.push(...walk(full));
    else out.push(full);
  }
  return out;
}

const build = spawnSync(process.execPath, [TSC, "-b"], {
  cwd: ROOT,
  stdio: "inherit",
});
if (build.status !== 0) {
  console.error("BUILD FAILED");
  process.exit(build.status ?? 1);
}

const compiled = walk(join(ROOT, "test", "dist")).filter((file) =>
  file.endsWith(".test.js"),
);
const source = walk(join(ROOT, "test"))
  .filter((file) => !file.startsWith(join(ROOT, "test", "dist")))
  .filter((file) => file.endsWith(".test.mjs"));
const testFiles = [...compiled, ...source];
if (testFiles.length === 0) {
  console.error("EMPTY TEST SET");
  process.exit(1);
}

console.log(`running ${testFiles.length} compiled test file(s)`);
const run = spawnSync(process.execPath, ["--test", ...testFiles], {
  cwd: ROOT,
  stdio: "inherit",
});
process.exit(run.status ?? 1);
