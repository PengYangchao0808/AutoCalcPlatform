#!/usr/bin/env node
/**
 * Produce one tarball per workspace package under `dist-packages/`.
 * Previous artifacts are removed first so the directory is deterministic.
 */

import { spawnSync } from "node:child_process";
import { mkdirSync, readdirSync, rmSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = fileURLToPath(new URL("..", import.meta.url));
const TSC = join(ROOT, "node_modules", "typescript", "bin", "tsc");
const OUT = join(ROOT, "dist-packages");
const PACKAGES = ["research", "bridge", "dsh-bundle", "cli"];

function run(command, args, cwd) {
  const result = spawnSync(command, args, { cwd, stdio: "inherit" });
  if (result.error !== undefined) {
    console.error(`failed to run ${command}: ${result.error.message}`);
    process.exit(1);
  }
  if (result.status !== 0) {
    process.exit(result.status ?? 1);
  }
}

function packCommand() {
  const execPath = process.env.npm_execpath;
  if (typeof execPath === "string" && execPath.endsWith(".cjs")) {
    return { command: process.execPath, prefix: [execPath] };
  }
  return { command: "pnpm", prefix: [] };
}

run(process.execPath, [TSC, "-b"], ROOT);

rmSync(OUT, { recursive: true, force: true });
mkdirSync(OUT, { recursive: true });

const { command, prefix } = packCommand();
for (const pkg of PACKAGES) {
  run(
    command,
    [...prefix, "pack", "--pack-destination", OUT],
    join(ROOT, "packages", pkg),
  );
}

const artifacts = readdirSync(OUT).sort();
if (artifacts.length !== PACKAGES.length) {
  console.error(
    `expected ${PACKAGES.length} packages, found ${artifacts.length}`,
  );
  process.exit(1);
}
console.log(`packaged ${artifacts.length} artifacts into ${OUT}`);
for (const artifact of artifacts) console.log(`  ${artifact}`);
