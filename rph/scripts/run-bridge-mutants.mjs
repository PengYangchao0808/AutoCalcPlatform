#!/usr/bin/env node
/**
 * Mutation proof for the bridge boundary guards (plan C T-10 / C06b).
 *
 * Each entry removes exactly one guard from
 * `packages/bridge/src/index.ts`, rebuilds, runs the single test that owns the
 * guard, and requires that test to go red. The source is restored byte-for-byte
 * after every mutant and the SHA-256 before/after is written to the evidence
 * matrix.
 *
 * Usage: node scripts/run-bridge-mutants.mjs --out <evidence-dir>
 */

import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import {
  mkdirSync,
  readFileSync,
  writeFileSync,
} from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = fileURLToPath(new URL("..", import.meta.url));
const SOURCE = join(ROOT, "packages", "bridge", "src", "index.ts");
const TSC = join(ROOT, "node_modules", "typescript", "bin", "tsc");
const TEST_FILE = join(ROOT, "test", "dist", "bridge-contract.test.js");

const MUTANTS = Object.freeze([
  {
    id: "raw_prefix",
    guard: "raw-prefix rejection",
    test: "model-visible prefix stripping is exactly once and raw prefixes are rejected",
    find: "  if (raw.includes(BRIDGE_MODEL_PREFIX)) {",
    replace: "  if (false && raw.includes(BRIDGE_MODEL_PREFIX)) {",
  },
  {
    id: "iserror_preserved",
    guard: "isError preservation",
    test: "tool isError is never promoted to success",
    find: '    outcome: result.isError ? "error" : "ok",',
    replace: '    outcome: "ok",',
  },
  {
    id: "read_before_prepare",
    guard: "read-before-prepare",
    test: "read before prepare returns the exact not_prepared envelope without preparing",
    find:
      "      if (this.requirePreparedRead && !this.prepared.has(artifactKey(request))) {\n        return notPreparedResult(request);\n      }",
    replace: "      if (false && this.requirePreparedRead) {\n        return notPreparedResult(request);\n      }",
  },
  {
    id: "operation_required_matrix",
    guard: "per-operation required matrix",
    test: "per-operation required-field matrix accepts positives and rejects every missing field",
    find: "    const missing = missingFieldsFor(request);",
    replace: "    const missing = [] as BridgeField[];",
  },
  {
    id: "artifact_ownership",
    guard: "project/attempt ownership",
    test: "cross-project and cross-attempt reads are refused",
    find: "      this.assertArtifactOwnership(request);",
    replace: "      if (false) this.assertArtifactOwnership(request);",
  },
  {
    id: "credential_absolute_path",
    guard: "credential/absolute-path refusal",
    test: "credential-bearing and absolute-path results are refused",
    find: "  assertResultRedacted(result.structuredContent);",
    replace: "  if (false) assertResultRedacted(result.structuredContent);",
  },
]);

function sha256(text) {
  return createHash("sha256").update(text).digest("hex");
}

function runTsc() {
  return spawnSync(process.execPath, [TSC, "-b"], {
    cwd: ROOT,
    encoding: "utf8",
  });
}

function runTest(pattern) {
  return spawnSync(
    process.execPath,
    ["--test", "--test-name-pattern", pattern, TEST_FILE],
    { cwd: ROOT, encoding: "utf8" },
  );
}

function outputOf(result) {
  return `${result.stdout ?? ""}${result.stderr ?? ""}`;
}

function parseOut(argv) {
  const index = argv.indexOf("--out");
  if (index === -1 || argv[index + 1] === undefined) {
    throw new Error("missing --out <evidence-dir>");
  }
  return argv[index + 1];
}

function writeMatrix(out, matrix) {
  writeFileSync(
    join(out, "mutants.json"),
    `${JSON.stringify(matrix, null, 2)}\n`,
    "utf8",
  );
  const lines = [
    "# Bridge mutant proof",
    "",
    `- source: \`${matrix.source}\``,
    `- sha256_before: \`${matrix.sha256_before}\``,
    `- sha256_after: \`${matrix.sha256_after}\``,
    `- restored: ${matrix.restored}`,
    `- expected_mutants: ${matrix.expected_mutants}`,
    `- red_mutants: ${matrix.red_mutants}`,
    "",
    "| mutant | guard | test_exit | red | restored |",
    "| --- | --- | --- | --- | --- |",
  ];
  for (const item of matrix.mutants) {
    lines.push(
      `| ${item.id} | ${item.guard} | ${item.test_exit} | ${item.red} | ${item.restored} |`,
    );
  }
  writeFileSync(join(out, "mutants.md"), `${lines.join("\n")}\n`, "utf8");
}

function main() {
  const out = parseOut(process.argv.slice(2));
  mkdirSync(out, { recursive: true });
  const original = readFileSync(SOURCE, "utf8");
  const before = sha256(original);
  const results = [];
  try {
    for (const mutant of MUTANTS) {
      const occurrences = original.split(mutant.find).length - 1;
      if (occurrences !== 1) {
        results.push({
          id: mutant.id,
          guard: mutant.guard,
          test: mutant.test,
          applied: false,
          occurrences,
          red: false,
          specific_test_failed: false,
          restored: true,
          build_exit: null,
          test_exit: null,
        });
        continue;
      }
      const mutated = original.replace(mutant.find, mutant.replace);
      writeFileSync(SOURCE, mutated, "utf8");
      const build = runTsc();
      const test = build.status === 0 ? runTest(mutant.test) : null;
      const combined = test === null ? "" : outputOf(test);
      const testExit = test === null ? null : test.status;
      const red =
        build.status === 0 && testExit !== 0 && testExit !== null;
      const specificTestFailed =
        red &&
        combined.includes("not ok") &&
        combined.includes(mutant.test);
      writeFileSync(
        join(out, `mutant-${mutant.id}.log`),
        [
          `mutant=${mutant.id}`,
          `guard=${mutant.guard}`,
          `test=${mutant.test}`,
          `build_exit=${build.status}`,
          `test_exit=${testExit}`,
          `specific_test_failed=${specificTestFailed}`,
          "",
          "--- build ---",
          outputOf(build),
          "--- test ---",
          combined,
        ].join("\n"),
        "utf8",
      );
      writeFileSync(SOURCE, original, "utf8");
      const restored = sha256(readFileSync(SOURCE, "utf8")) === before;
      results.push({
        id: mutant.id,
        guard: mutant.guard,
        test: mutant.test,
        applied: true,
        occurrences,
        red,
        specific_test_failed: specificTestFailed,
        restored,
        build_exit: build.status,
        test_exit: testExit,
      });
    }
  } finally {
    writeFileSync(SOURCE, original, "utf8");
    runTsc();
  }
  const after = sha256(readFileSync(SOURCE, "utf8"));
  const matrix = {
    schema: "acp2.c.bridge-mutant-proof.v1",
    generated_at: new Date().toISOString(),
    source: "rph/packages/bridge/src/index.ts",
    sha256_before: before,
    sha256_after: after,
    restored: before === after,
    expected_mutants: MUTANTS.length,
    red_mutants: results.filter(
      (item) => item.red && item.specific_test_failed && item.restored,
    ).length,
    mutants: results,
  };
  writeMatrix(out, matrix);
  const ok =
    matrix.restored && matrix.red_mutants === matrix.expected_mutants;
  process.stdout.write(`${JSON.stringify(matrix, null, 2)}\n`);
  process.exit(ok ? 0 : 1);
}

main();
