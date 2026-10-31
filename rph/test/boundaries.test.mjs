/**
 * M3-02 — the boundary gate itself.
 *
 * Five negative classes are now permanent tests, each against a synthetic
 * workspace (so a rule can never rot into documentation):
 *
 *   1. direct network (`fetch`, `undici`, `node:http(s)`, bare `http`, `ws`);
 *   2. a listening server / a second Controller (`createServer`, `.listen`);
 *   3. a second `mcp serve` argv outside the named launcher;
 *   4. a non-launcher process call (`spawn`/`exec`, `child_process`);
 *   5. an import against the frozen direction (`cli -> bridge`, `bridge ->
 *      dsh-bundle`, `research -> bridge`).
 *
 * The real tree must stay green, and the launcher itself must keep its
 * allowance — otherwise "only one launcher" would be enforced by luck.
 */

import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { PACKAGES, checkBoundaries } from "../scripts/check-boundaries.mjs";

const ROOT = fileURLToPath(new URL("..", import.meta.url));

const BASE_SOURCES = {
  research: "export const research = 1;\n",
  bridge: 'import { research } from "@rph/research";\nexport const bridge = research;\n',
  "dsh-bundle": 'import { bridge } from "@rph/bridge";\nexport const bundle = bridge;\n',
  cli: 'import { bundle } from "@rph/dsh-bundle";\nexport const cli = bundle;\n',
};

/** A throwaway workspace with the four package source roots. */
function workspace() {
  const root = mkdtempSync(join(tmpdir(), "rph-boundaries-"));
  for (const pkg of PACKAGES) {
    const src = join(root, "packages", pkg, "src");
    mkdirSync(src, { recursive: true });
    writeFileSync(join(src, "index.ts"), BASE_SOURCES[pkg]);
  }
  return root;
}

function addFile(root, relativePath, body) {
  const path = join(root, relativePath);
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, body);
  return path;
}

function rulesFor(root, file) {
  return checkBoundaries(root)
    .findings.filter((finding) => finding.file === file)
    .map((finding) => finding.rule);
}

function assertRule(root, relativePath, body, rule) {
  addFile(root, relativePath, body);
  const rules = rulesFor(root, relativePath);
  assert.equal(
    rules.includes(rule),
    true,
    `${relativePath} did not trigger ${rule} (got ${rules.join(",") || "nothing"})`,
  );
}

test("the real workspace passes the gate", () => {
  const { findings, scanned } = checkBoundaries(ROOT);
  assert.deepEqual(findings, []);
  assert.equal(scanned.length > 20, true);
  assert.equal(
    scanned.every((file) => file.startsWith("packages/")),
    true,
  );
});

test("a synthetic workspace with the frozen direction passes", () => {
  const root = workspace();
  assert.deepEqual(checkBoundaries(root).findings, []);
});

test("a package with no sources fails", () => {
  const root = mkdtempSync(join(tmpdir(), "rph-boundaries-empty-"));
  mkdirSync(join(root, "packages", "cli", "src"), { recursive: true });
  writeFileSync(join(root, "packages", "cli", "src", "index.ts"), "export {};\n");
  const findings = checkBoundaries(root).findings;
  assert.equal(findings.some((entry) => entry.rule === "missing_src"), true);
});

test("direct network clients are refused in every package", () => {
  const root = workspace();
  assertRule(root, "packages/research/src/net.ts", 'const r = await fetch("http://x");\n', "fetch");
  assertRule(root, "packages/bridge/src/undici.ts", 'import { request } from "undici";\n', "undici");
  assertRule(root, "packages/bridge/src/node-http.ts", 'import http from "node:http";\n', "node_http");
  assertRule(root, "packages/dsh-bundle/src/bare-http.ts", 'const http = require("https");\n', "bare_net_module");
  assertRule(root, "packages/cli/src/ws.ts", 'import { WebSocket } from "ws";\n', "websocket");
});

test("a listening server (a second Controller) is refused", () => {
  const root = workspace();
  assertRule(
    root,
    "packages/dsh-bundle/src/server.ts",
    'import { createServer } from "node:net";\nconst server = createServer(() => {});\n',
    "second_controller",
  );
  assertRule(root, "packages/cli/src/listen.ts", "export const go = (s) => s.listen(8765);\n", "second_controller");
});

test("a second mcp serve argv is refused outside the named launcher", () => {
  const root = workspace();
  assertRule(
    root,
    "packages/cli/src/serve.ts",
    'export const args = ["-m", "acp", "mcp", "serve", "--controller", "http://x"];\n',
    "second_controller_launch",
  );
  // Prose is not a launch.
  addFile(root, "packages/cli/src/comment.ts", "/** The launcher spells `acp mcp serve` once. */\nexport const x = 1;\n");
  assert.deepEqual(rulesFor(root, "packages/cli/src/comment.ts"), []);
});

test("a process call is refused outside the named launcher", () => {
  const root = workspace();
  assertRule(
    root,
    "packages/cli/src/spawn.ts",
    'import { spawn } from "node:child_process";\nexport const go = () => spawn("acp", []);\n',
    "child_process",
  );
  assertRule(root, "packages/dsh-bundle/src/exec.ts", 'export const go = () => exec("ls");\n', "exec");
});

test("the named launcher keeps its allowance and gains nothing else", () => {
  const root = workspace();
  const launcher = "packages/dsh-bundle/src/mcp-stdio-launch.ts";
  addFile(
    root,
    launcher,
    [
      'import { spawn } from "node:child_process";',
      'export const args = ["-m", "acp", "mcp", "serve", "--controller", "http://x"];',
      'export const go = (cmd) => spawn(cmd, args);',
      "",
    ].join("\n"),
  );
  assert.deepEqual(rulesFor(root, launcher), []);
  // The launcher may start a process; it may not open a socket or a server.
  addFile(root, "packages/dsh-bundle/src/mcp-stdio-launch.ts", [
    'import { spawn } from "node:child_process";',
    'export const args = ["-m", "acp", "mcp", "serve"];',
    "export const go = (cmd) => { fetch('http://x'); return spawn(cmd, args); };",
    "",
  ].join("\n"));
  const rules = rulesFor(root, launcher);
  assert.equal(rules.includes("fetch"), true);
  assert.equal(rules.includes("spawn"), false, "the launcher keeps the process allowance");
});

test("wrong-direction imports are refused", () => {
  const root = workspace();
  assertRule(
    root,
    "packages/cli/src/direct-bridge.ts",
    'import { Bridge } from "@rph/bridge";\nexport const b = Bridge;\n',
    "wrong_import_direction",
  );
  assertRule(
    root,
    "packages/cli/src/direct-research.ts",
    'import "@rph/research";\n',
    "wrong_import_direction",
  );
  assertRule(
    root,
    "packages/bridge/src/upward.ts",
    'import { bundle } from "@rph/dsh-bundle";\nexport const b = bundle;\n',
    "wrong_import_direction",
  );
  assertRule(
    root,
    "packages/research/src/upward.ts",
    'import { bridge } from "@rph/bridge";\nexport const b = bridge;\n',
    "wrong_import_direction",
  );
});

test("the real cli imports only the bundle", () => {
  const { findings, scanned } = checkBoundaries(ROOT);
  assert.deepEqual(findings, []);
  const cli = scanned.filter((file) => file.startsWith("packages/cli/"));
  assert.equal(cli.length > 0, true);
});
