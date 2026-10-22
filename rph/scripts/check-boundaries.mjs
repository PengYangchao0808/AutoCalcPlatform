#!/usr/bin/env node
/**
 * Static boundary gate for the RPH workspace.
 *
 * Scans `packages/<name>/src/**` and fails (exit 1) when a package:
 *   - reaches for an execution/private surface (child_process, spawn/exec,
 *     SSH, direct net, DSH private source paths, platform store imports), or
 *   - imports across the frozen direction cli -> dsh-bundle -> bridge -> research.
 *
 * Scientific software names (orca, xtb, crest, ...) are plain data/metadata and
 * are intentionally NOT matched.
 */

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = fileURLToPath(new URL("..", import.meta.url));
const PACKAGES = ["research", "bridge", "dsh-bundle", "cli"];
const SOURCE_EXTENSIONS = new Set([".ts", ".tsx", ".js", ".mjs"]);

/** Allowed direct imports per package (self is allowed). */
const ALLOWED_IMPORTS = Object.freeze({
  research: new Set(["research"]),
  bridge: new Set(["bridge", "research"]),
  "dsh-bundle": new Set(["dsh-bundle", "bridge"]),
  cli: new Set(["cli", "dsh-bundle"]),
});

const EXECUTION_RULES = [
  { id: "child_process", test: (text) => /node:child_process|["']child_process["']/.test(text) },
  { id: "spawn", test: (text) => /\.spawn\s*\(/.test(text) },
  { id: "exec", test: (text) => /\.exec\s*\(/.test(text) },
  { id: "execFile", test: (text) => /\.execFile\s*\(/.test(text) },
  { id: "ssh2", test: (text) => /["']ssh2["']/.test(text) },
  { id: "dsh_private_source", test: (text) => /deepseek-harness\/apps\/|deepseek-harness\/packages\//.test(text) },
  { id: "platform_store", test: (text) => /\bacp_services\b|scheduler\.store|resource-store/.test(text) },
];

const DIRECT_NET_RULE = {
  id: "direct_net",
  test: (text) => /node:net|["']net["']/.test(text),
};

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
    const info = statSync(full);
    if (info.isDirectory()) {
      out.push(...walk(full));
    } else if (SOURCE_EXTENSIONS.has(full.slice(full.lastIndexOf(".")))) {
      out.push(full);
    }
  }
  return out;
}

function importSpecifiers(text) {
  const specs = new Set();
  const patterns = [
    /\bfrom\s+["']([^"']+)["']/g,
    /\bimport\s*\(\s*["']([^"']+)["']\s*\)/g,
    /\brequire\s*\(\s*["']([^"']+)["']\s*\)/g,
    /\bimport\s+["']([^"']+)["']/g,
  ];
  for (const pattern of patterns) {
    let match;
    while ((match = pattern.exec(text)) !== null) {
      if (match[1] !== undefined) specs.add(match[1]);
    }
  }
  return [...specs];
}

function targetPackage(specifier) {
  if (specifier.startsWith("@rph/")) {
    const name = specifier.slice("@rph/".length).split("/")[0];
    return PACKAGES.includes(name) ? name : null;
  }
  const segments = specifier.replaceAll("\\", "/").split("/");
  return segments.find((segment) => PACKAGES.includes(segment)) ?? null;
}

const findings = [];
const scanned = [];

for (const pkg of PACKAGES) {
  const srcDir = join(ROOT, "packages", pkg, "src");
  const files = walk(srcDir);
  if (files.length === 0) {
    findings.push({ package: pkg, file: "-", rule: "missing_src", detail: "no source files" });
    continue;
  }
  for (const file of files) {
    const rel = relative(ROOT, file);
    scanned.push(rel);
    const text = readFileSync(file, "utf8");

    for (const rule of EXECUTION_RULES) {
      if (rule.test(text)) {
        findings.push({ package: pkg, file: rel, rule: rule.id, detail: "forbidden execution/private surface" });
      }
    }
    if ((pkg === "bridge" || pkg === "research") && DIRECT_NET_RULE.test(text)) {
      findings.push({ package: pkg, file: rel, rule: "direct_net", detail: "bridge/research must not open sockets" });
    }

    for (const specifier of importSpecifiers(text)) {
      if (specifier.startsWith("@deepseek-ai/dsh-")) {
        const allowed = pkg === "dsh-bundle" && specifier === "@deepseek-ai/dsh-mcp-client";
        if (!allowed) {
          findings.push({ package: pkg, file: rel, rule: "dsh_private_import", detail: `import ${specifier}` });
        }
        continue;
      }
      const target = targetPackage(specifier);
      if (target === null) continue;
      const allowed = ALLOWED_IMPORTS[pkg];
      if (!allowed.has(target)) {
        findings.push({
          package: pkg,
          file: rel,
          rule: "wrong_import_direction",
          detail: `${pkg} -> ${target} (${specifier})`,
        });
      }
    }
  }
}

console.log("RPH boundary matrix");
console.log(`  packages: ${PACKAGES.join(", ")}`);
console.log(`  scanned files: ${scanned.length}`);
for (const pkg of PACKAGES) {
  const own = scanned.filter((file) => file.startsWith(`packages/${pkg}/`));
  console.log(`  ${pkg}: ${own.length} file(s), imports -> ${[...ALLOWED_IMPORTS[pkg]].join(", ")}`);
}
console.log("  forbidden: child_process, spawn/exec/execFile, ssh2, node:net (bridge/research),");
console.log("             DSH private source paths, platform store imports, wrong-direction imports");

if (findings.length > 0) {
  console.error("\nBOUNDARY CHECK FAILED");
  for (const finding of findings) {
    console.error(`  - [${finding.rule}] ${finding.file}: ${finding.detail}`);
  }
  process.exit(1);
}

console.log("\nBOUNDARY CHECK PASS");
