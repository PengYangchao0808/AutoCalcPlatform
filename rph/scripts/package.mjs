#!/usr/bin/env node
/**
 * Produce one tarball per workspace package under `dist-packages/`, then build
 * the self-contained offline install surface next to them.
 *
 * The surface is what makes the artifact set installable on a clean host:
 * `rph-install.package.json` + `package.json`, `pnpm-workspace.yaml`
 * (overrides for transitive private dependencies), a generated
 * `pnpm-lock.yaml`, `rph-packages.json`, `SHA256SUMS`, `README.md` and
 * `verify-install.mjs`.
 *
 * Packaging fails unless:
 *   - exactly the four expected tarballs were produced;
 *   - every tarball digest matches the artifact index;
 *   - the generated lockfile resolves `@rph/*` only through `file:` tarballs;
 *   - a throwaway copy of the directory installs with
 *     `pnpm install --offline --frozen-lockfile` and imports every package
 *     from inside that copy.
 */

import { spawnSync } from "node:child_process";
import {
  copyFileSync,
  cpSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import {
  INSTALL_LOCKFILE_NAME,
  PACKAGE_NAMES,
  VERIFY_SCRIPT_NAME,
  assertNoRegistryResolution,
  sha256File,
  tarballName,
  verifyInstallSurface,
  writeChecksums,
  writeInstallSurface,
} from "./install-surface.mjs";

const ROOT = fileURLToPath(new URL("..", import.meta.url));
const TSC = join(ROOT, "node_modules", "typescript", "bin", "tsc");
const OUT = join(ROOT, "dist-packages");

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

/** pnpm entry point for nested install runs (lock generation, smoke install). */
function pnpmCommand() {
  const { command, prefix } = packCommand();
  return { command, prefix };
}

function pnpm(args, cwd) {
  const { command, prefix } = pnpmCommand();
  run(command, [...prefix, ...args], cwd);
}

function readWorkspacePackages() {
  return PACKAGE_NAMES.map((name) => {
    const manifestPath = join(ROOT, "packages", name, "package.json");
    const manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
    if (typeof manifest.version !== "string" || manifest.version.length === 0) {
      console.error(`${manifestPath} has no version`);
      process.exit(1);
    }
    if (manifest.private !== true) {
      console.error(`${manifestPath} must stay private (tarball distribution only)`);
      process.exit(1);
    }
    for (const required of ["LICENSE", "NOTICE"]) {
      if (!existsSync(join(ROOT, "packages", name, required))) {
        console.error(`${manifestPath} is missing ${required}`);
        process.exit(1);
      }
    }
    const bin =
      manifest.bin === undefined ? null : Object.keys(manifest.bin)[0] ?? null;
    return {
      name,
      version: manifest.version,
      file: tarballName(name, manifest.version),
      license: manifest.license ?? null,
      bin,
    };
  });
}

function assertPackedArtifacts(packages) {
  const packed = readdirSync(OUT)
    .filter((file) => file.endsWith(".tgz"))
    .sort();
  const expected = packages.map((entry) => entry.file).sort();
  if (JSON.stringify(packed) !== JSON.stringify(expected)) {
    console.error("packed tarball set does not match the workspace:");
    console.error(`  expected: ${expected.join(", ") || "(none)"}`);
    console.error(`  packed:   ${packed.join(", ") || "(none)"}`);
    process.exit(1);
  }
}

/** The tarball itself must carry the license/notice/bin/profile surface. */
function assertPackedContents(packages) {
  const required = {
    "research": [],
    "bridge": [],
    "dsh-bundle": ["package/profiles/web.cordis.yml", "package/profiles/headless.cordis.yml"],
    "cli": ["package/dist/main.js"],
  };
  for (const entry of packages) {
    const path = join(OUT, entry.file);
    const listed = spawnSync("tar", ["-tzf", path], { encoding: "utf8" });
    if (listed.status !== 0) {
      console.error(`could not list ${entry.file}: ${listed.stderr}`);
      process.exit(1);
    }
    const members = new Set(listed.stdout.split("\n").filter(Boolean));
    const needed = [
      "package/package.json",
      "package/LICENSE",
      "package/NOTICE",
      ...required[entry.name],
    ];
    const missing = needed.filter((name) => !members.has(name));
    if (missing.length > 0) {
      console.error(`${entry.file} is missing ${missing.join(", ")}`);
      process.exit(1);
    }
    if (entry.name === "cli") {
      const manifest = JSON.parse(
        readFileSync(join(ROOT, "packages", "cli", "package.json"), "utf8"),
      );
      if (manifest.bin?.rph !== "./dist/main.js") {
        console.error("@rph/cli must publish the `rph` bin pointing at ./dist/main.js");
        process.exit(1);
      }
    }
  }
}

/** Generate pnpm-lock.yaml from the local tarballs, in a workspace-free dir. */
function generateLockfile(packages) {
  const stage = mkdtempSync(join(tmpdir(), "rph-lock-"));
  try {
    for (const file of readdirSync(OUT)) {
      if (file === INSTALL_LOCKFILE_NAME) continue;
      copyFileSync(join(OUT, file), join(stage, file));
    }
    pnpm(["install", "--lockfile-only", "--offline", "--ignore-scripts"], stage);
    const lock = readFileSync(join(stage, INSTALL_LOCKFILE_NAME), "utf8");
    assertNoRegistryResolution(lock);
    writeFileSync(join(OUT, INSTALL_LOCKFILE_NAME), lock);
    return lock;
  } finally {
    rmSync(stage, { recursive: true, force: true });
  }
}

/**
 * Copy the install surface to a throwaway directory and install it exactly the
 * way a clean host would: offline, frozen lockfile, no source checkout.
 */
function cleanInstallSmoke() {
  const stage = mkdtempSync(join(tmpdir(), "rph-install-"));
  try {
    cpSync(OUT, stage, {
      recursive: true,
      filter: (source) => !source.includes("node_modules"),
    });
    pnpm(
      ["install", "--offline", "--frozen-lockfile", "--ignore-scripts"],
      stage,
    );
    run(process.execPath, [join(stage, VERIFY_SCRIPT_NAME)], stage);
  } finally {
    rmSync(stage, { recursive: true, force: true });
  }
}

run(process.execPath, [TSC, "-b"], ROOT);

rmSync(OUT, { recursive: true, force: true });
mkdirSync(OUT, { recursive: true });

const packages = readWorkspacePackages();
const { command, prefix } = packCommand();
for (const name of PACKAGE_NAMES) {
  run(
    command,
    [...prefix, "pack", "--pack-destination", OUT],
    join(ROOT, "packages", name),
  );
}
assertPackedArtifacts(packages);
assertPackedContents(packages);

const artifacts = packages.map((entry) => {
  const path = join(OUT, entry.file);
  return {
    ...entry,
    size: statSync(path).size,
    sha256: sha256File(path),
  };
});

writeInstallSurface(OUT, artifacts);
generateLockfile(artifacts);
writeChecksums(OUT);
const index = verifyInstallSurface(OUT);
cleanInstallSmoke();

console.log(`packaged ${artifacts.length} artifacts into ${OUT}`);
for (const artifact of index.packages) {
  console.log(`  ${artifact.file}  ${artifact.size} bytes  sha256=${artifact.sha256}`);
}
console.log("offline install surface:");
for (const file of readdirSync(OUT).sort()) {
  if (!file.endsWith(".tgz")) console.log(`  ${file}`);
}
console.log("OFFLINE INSTALL SURFACE VERIFIED");
