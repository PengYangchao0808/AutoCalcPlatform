/**
 * Guard the offline install surface against regressing to registry lookups.
 *
 * The 2026-10-26 clean-host probe failed because `pnpm add file:*.tgz` made
 * pnpm resolve `@rph/research` on registry.npmjs.org (ERR_PNPM_FETCH_404).
 * These tests pin the three places that keep the private packages local:
 *
 *   1. workspace manifests keep `workspace:*` (npm pack rewrites it to a plain
 *      version, so the tarball's internal dependency is registry-shaped);
 *   2. the generated consumer manifest and `pnpm-workspace.yaml` overrides map
 *      every `@rph/*` name to a local tarball;
 *   3. the generated lockfile may not contain registry resolutions.
 */

import assert from "node:assert/strict";
import { readFileSync, rmSync, writeFileSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import {
  INSTALL_MANIFEST_NAME,
  INSTALL_WORKSPACE_NAME,
  NODE_ENGINE,
  PACKAGE_MANAGER,
  PACKAGE_NAMES,
  RPH_SCOPE,
  assertNoRegistryResolution,
  assertNoRegistrySpecifiers,
  assertOverridesCoverage,
  buildInstallManifest,
  buildPackageIndex,
  internalPackageName,
  renderInstallWorkspace,
  sha256File,
  tarballName,
  verifyChecksums,
  writeChecksums,
} from "../scripts/install-surface.mjs";

const ROOT = fileURLToPath(new URL("..", import.meta.url));
const WORKSPACE_MANIFESTS = PACKAGE_NAMES.map((name) => ({
  name,
  path: join(ROOT, "packages", name, "package.json"),
}));

function fixturePackages(version = "0.0.0") {
  return PACKAGE_NAMES.map((name) => ({
    name,
    version,
    file: tarballName(name, version),
    size: 1024,
    sha256: "0".repeat(64),
  }));
}

function lockfileFor(packages, { registryUrl = "", plainVersions = false } = {}) {
  const entries = packages.map((entry) => {
    const key = plainVersions
      ? `${internalPackageName(entry.name)}@${entry.version}`
      : `${internalPackageName(entry.name)}@file:${entry.file}`;
    return `  '${key}':\n    resolution: {integrity: sha512-deadbeef, tarball: file:${entry.file}}`;
  });
  return [
    "lockfileVersion: '9.0'",
    registryUrl === "" ? "" : `# ${registryUrl}`,
    "packages:",
    entries.join("\n"),
    "snapshots:",
    entries.join("\n"),
    "",
  ].join("\n");
}

test("workspace manifests keep internal dependencies on workspace:*", () => {
  for (const { name, path } of WORKSPACE_MANIFESTS) {
    const manifest = JSON.parse(readFileSync(path, "utf8"));
    for (const [dependency, specifier] of Object.entries(
      manifest.dependencies ?? {},
    )) {
      assert.equal(
        dependency.startsWith(RPH_SCOPE),
        true,
        `${name} has a non-@rph dependency ${dependency}`,
      );
      assert.equal(
        specifier,
        "workspace:*",
        `${name}: ${dependency} must stay workspace:* (npm pack rewrites it to a registry-shaped version)`,
      );
    }
  }
});

test("install surface pins the workspace toolchain", () => {
  const root = JSON.parse(readFileSync(join(ROOT, "package.json"), "utf8"));
  assert.equal(PACKAGE_MANAGER, root.packageManager);
  assert.equal(NODE_ENGINE, root.engines.node);
});

test("packed tarball names match the workspace versions", () => {
  for (const { name, path } of WORKSPACE_MANIFESTS) {
    const manifest = JSON.parse(readFileSync(path, "utf8"));
    assert.equal(tarballName(name, manifest.version), `rph-${name}-${manifest.version}.tgz`);
    assert.equal(manifest.private, true, `${name} must stay private`);
  }
});

test("install manifest maps every private package to a local tarball", () => {
  const manifest = buildInstallManifest(fixturePackages());
  assert.equal(manifest.private, true);
  assert.equal(manifest.packageManager.length > 0, true);
  assert.equal(Object.hasOwn(manifest, "pnpm"), false);
  assert.deepEqual(Object.keys(manifest.dependencies).sort(), PACKAGE_NAMES.map(internalPackageName).sort());
  for (const specifier of Object.values(manifest.dependencies)) {
    assert.equal(specifier.startsWith("file:./"), true);
    assert.equal(specifier.endsWith(".tgz"), true);
  }
  assertNoRegistrySpecifiers(manifest);
});

test("registry-shaped specs and unknown packages are rejected", () => {
  const packages = fixturePackages();
  const manifest = buildInstallManifest(packages);
  assert.throws(
    () =>
      assertNoRegistrySpecifiers({
        ...manifest,
        dependencies: { ...manifest.dependencies, "@rph/research": "0.0.0" },
      }),
    /resolved from a registry/,
  );
  assert.throws(
    () =>
      assertNoRegistrySpecifiers({
        ...manifest,
        dependencies: { ...manifest.dependencies, "@rph/research": "^0.0.0" },
      }),
    /resolved from a registry/,
  );
  assert.throws(() => buildInstallManifest(packages.slice(0, 3)), /missing package/);
  assert.throws(
    () => buildInstallManifest([...packages, { ...packages[0], name: "other" }]),
    /unknown package/,
  );
});

test("install workspace overrides cover all transitive @rph packages", () => {
  const packages = fixturePackages();
  const workspace = renderInstallWorkspace(packages);
  assertOverridesCoverage(workspace, packages);
  for (const name of PACKAGE_NAMES) {
    assert.equal(
      workspace.includes(`"${internalPackageName(name)}": "file:./${tarballName(name, "0.0.0")}"`),
      true,
      `workspace override missing for @rph/${name}`,
    );
  }
  const broken = workspace.replace(/^ {2}"@rph\/research".*\n/m, "");
  assert.throws(() => assertOverridesCoverage(broken, packages), /missing an override/);
  const registry = workspace.replace(
    `"${internalPackageName("research")}": "file:./${tarballName("research", "0.0.0")}"`,
    `"${internalPackageName("research")}": "0.0.0"`,
  );
  assert.throws(() => assertOverridesCoverage(registry, packages), /override|expected 4/);
});

test("lockfile guard accepts file:-only resolutions", () => {
  const lock = lockfileFor(fixturePackages());
  assertNoRegistryResolution(lock);
});

test("lockfile guard rejects registry and semver resolutions", () => {
  const packages = fixturePackages();
  assert.throws(
    () =>
      assertNoRegistryResolution(
        lockfileFor(packages, { registryUrl: "https://registry.npmjs.org/@rph%2Fresearch" }),
      ),
    /public registry/,
  );
  assert.throws(
    () => assertNoRegistryResolution(lockfileFor(packages, { plainVersions: true })),
    /not pinned to a local tarball/,
  );
  const missing = lockfileFor(packages).replace(
    /^ {2}'@rph\/research@file:.*\n {4}resolution.*\n/gm,
    "",
  );
  assert.throws(() => assertNoRegistryResolution(missing), /missing a file: entry/);
  assert.throws(() => assertNoRegistryResolution("lockfileVersion: '9.0'\n"), /no @rph\/\* entries/);
});

test("package index records name/version/size/sha256 for every tarball", () => {
  const index = buildPackageIndex(fixturePackages());
  assert.equal(index.schema, "acp2.rph.packages.v1");
  assert.equal(index.install_manifest, INSTALL_MANIFEST_NAME);
  assert.equal(index.install_workspace, INSTALL_WORKSPACE_NAME);
  assert.equal(index.packages.length, PACKAGE_NAMES.length);
  assert.deepEqual(
    index.packages.map((entry) => entry.name),
    [...PACKAGE_NAMES],
  );
  for (const entry of index.packages) {
    assert.match(entry.sha256, /^[0-9a-f]{64}$/);
    assert.equal(entry.size > 0, true);
  }
});

test("package.mjs wires the install surface into packaging", () => {
  const script = readFileSync(join(ROOT, "scripts", "package.mjs"), "utf8");
  for (const marker of [
    "writeInstallSurface",
    "writeChecksums",
    "verifyInstallSurface",
    "assertNoRegistryResolution",
    "--lockfile-only",
    "--offline",
    "--frozen-lockfile",
    INSTALL_MANIFEST_NAME,
  ]) {
    assert.equal(script.includes(marker), true, `package.mjs must reference ${marker}`);
  }
  assert.equal(
    script.includes("VERIFY_SCRIPT_NAME"),
    true,
    "package.mjs must run the shipped verify script by its canonical name",
  );
  assert.equal(script.includes("overrides:"), false, "overrides belong to install-surface.mjs");
});

test("checksums cover every shipped file and detect tampering", () => {
  const directory = mkdtempSync(join(tmpdir(), "rph-sums-"));
  try {
    writeFileSync(join(directory, "a.tgz"), "artifact-a");
    writeFileSync(join(directory, "b.json"), "{}\n");
    const lines = writeChecksums(directory);
    assert.equal(lines.length, 2);
    for (const line of lines) assert.match(line, /^[0-9a-f]{64} {2}\S+$/);
    assert.equal(verifyChecksums(directory).length, 2);
    assert.equal(sha256File(join(directory, "a.tgz")).length, 64);
    writeFileSync(join(directory, "a.tgz"), "tampered");
    assert.throws(() => verifyChecksums(directory), /sha256 mismatch/);
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});
