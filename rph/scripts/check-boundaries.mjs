#!/usr/bin/env node
/**
 * Static boundary gate for the RPH workspace.
 *
 * Scans `packages/<name>/{src,skills}/**` and reports (exit 1) when a package:
 *   - reaches for an execution/private surface (child_process, spawn/exec,
 *     SSH, DSH private source paths, platform store imports),
 *   - opens a network connection of its own (`fetch`, `undici`,
 *     `node:http(s)`/`net`/`tls`/`dgram`, `ws`, `WebSocket`) — the platform is
 *     reached over the injected MCP stdio transport, never over HTTP;
 *   - opens a listening server (`createServer`/`.listen`) or spells a second
 *     `mcp serve` child: there is exactly one launcher and no second
 *     Controller,
 *   - imports across the frozen direction cli -> dsh-bundle -> bridge -> research.
 *
 * The only exemptions are the **named launcher** (`mcp-stdio-launch.ts`), which
 * may start a client process, and the single evidence-model owner. Adding a
 * launcher is a deliberate gate change (the list below), never an implicit
 * allowance.
 *
 * `checkBoundaries(root)` is exported so `test/boundaries.test.mjs` can run the
 * gate against synthetic trees (negative cases stay permanent tests).
 *
 * Scientific software names (orca, xtb, crest, ...) are plain data/metadata and
 * are intentionally NOT matched.
 */

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

export const PACKAGES = ["research", "bridge", "dsh-bundle", "cli"];
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
  // A *process* call, not `regexp.exec(...)`/`server.listen(...)`: the callee is
  // a bare identifier (destructured child_process) or reached through a module
  // object (already caught by the child_process rule).
  { id: "spawn", test: (text) => /(?:^|[^.\w])spawn(?:Sync)?\s*\(/m.test(text) },
  {
    id: "exec",
    test: (text) => /(?:^|[^.\w])exec(?:Sync|File|FileSync)?\s*\(/m.test(text),
  },
  { id: "ssh2", test: (text) => /["']ssh2["']/.test(text) },
  { id: "dsh_private_source", test: (text) => /deepseek-harness\/apps\/|deepseek-harness\/packages\//.test(text) },
  { id: "platform_store", test: (text) => /\bacp_services\b|scheduler\.store|resource-store/.test(text) },
];

// ---------------------------------------------------------------------------
// M3-02 — direct network, listening servers and the single Controller launcher
// ---------------------------------------------------------------------------
//
// RPH talks to the platform through the injected MCP stdio transport. Any
// direct network client, any listening server, and any second `mcp serve`
// spelling is a hard failure in every package (no allowlist for network), so
// "the bridge never does HTTP" and "there is no hidden second Controller" are
// enforced by the gate instead of being documentation.

const NETWORK_RULES = [
  { id: "fetch", test: (text) => /\bfetch\s*\(/.test(text) },
  { id: "undici", test: (text) => /["']undici["']/.test(text) },
  {
    id: "node_http",
    test: (text) => /["']node:(?:http2?|https|net|tls|dgram)["']/.test(text),
  },
  {
    id: "bare_net_module",
    test: (text) => /["'](?:http2?|https|net|tls|dgram)["']/.test(text),
  },
  { id: "websocket", test: (text) => /["']ws["']|\bnew WebSocket\s*\(/.test(text) },
];

const SERVER_RULE = {
  id: "second_controller",
  test: (text) => /\bcreateServer\s*\(|\.listen\s*\(/.test(text),
};

/**
 * A second `mcp serve` child, written as an argument vector.
 *
 * Matches `"mcp", "serve"` (any quoting/spacing) and never the prose
 * `acp mcp serve` in a doc comment.
 */
const MCP_SERVE_RULE = {
  id: "second_controller_launch",
  test: (text) => /["']mcp["'][\s,]*["']serve["']/.test(text),
};

/**
 * The named launcher: the only file that may start a client process or spell
 * the `mcp serve` argv. Everything else stays a hard failure.
 */
const LAUNCHER_ALLOWLIST = new Map([
  [
    "packages/dsh-bundle/src/mcp-stdio-launch.ts",
    new Set(["child_process", "spawn", "exec", "execFile", "second_controller_launch"]),
  ],
]);

// ---------------------------------------------------------------------------
// M1-03 — exactly one evidence/report model per layer
// ---------------------------------------------------------------------------
//
// Three authorities exist and no fourth may appear:
//   1. `src/cccp/analysis/research/evidence.py` (Python, science schema — A),
//   2. `src/acp_services/runtime/evidence_projection.py` (Python, projection — B),
//   3. `packages/research/src/evidence.ts` (TypeScript, the RPH view — C).
// `packages/dsh-bundle/skills/models.ts` is a *declared legacy* model-facing
// skill I/O schema (`acp2.evidence.report.v1`, owner M3-04). It is never
// persisted, never digest-bound and can never be imported by bridge/research
// (the import-direction rule above already forbids that direction). Converging
// it onto the single view is tracked by ADR-003 as a leftover, not silently
// allowlisted: any *new* module fails this check until the guard and the ADR
// are changed deliberately.
const EVIDENCE_MODEL_ALLOWLIST = new Map([
  [
    "packages/research/src/evidence.ts",
    "the single RPH EvidenceView/ResearchDecision model (M1-03)",
  ],
  [
    "packages/dsh-bundle/skills/models.ts",
    "declared legacy skill I/O schema acp2.evidence.report.v1 (owner M3-04)",
  ],
]);

const EVIDENCE_MODEL_MARKERS = [
  { id: "claim_status_vocabulary", test: (text) => /EVIDENCE_CLAIM_STATUSES/.test(text) },
  { id: "report_interface", test: (text) => /export interface EvidenceReport\b/.test(text) },
  {
    id: "report_validator",
    test: (text) => /export function (validate|assert)EvidenceReport\s*\(/.test(text),
  },
];

// The bridge is a parse/attribute adapter: it must import the model, not
// restate it.
const BRIDGE_MODEL_REDECLARATION = [
  {
    id: "bridge_evidence_model",
    test: (text) =>
      /export (interface|type|class) (EvidenceView|TaskRef|ResearchDecision|DecisionRecord)\b/.test(
        text,
      ),
  },
  {
    id: "bridge_evidence_policy",
    test: (text) =>
      /export (const|function) (validateEvidenceView|buildDecisionRecord|parseTaskRef)\b/.test(
        text,
      ),
  },
];

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

/**
 * Run the whole gate against `root` (a workspace root with `packages/`).
 *
 * Pure with respect to the filesystem (read-only); returns findings instead of
 * exiting so negative tests can assert individual rule ids.
 */
export function checkBoundaries(root) {
  const findings = [];
  const scanned = [];

  for (const pkg of PACKAGES) {
    const sourceDirs = ["src", "skills"].map((name) => join(root, "packages", pkg, name));
    const files = sourceDirs.flatMap((dir) => walk(dir));
    if (files.length === 0) {
      findings.push({ package: pkg, file: "-", rule: "missing_src", detail: "no source files" });
      continue;
    }
    for (const file of files) {
      const rel = relative(root, file).replaceAll("\\", "/");
      scanned.push(rel);
      const text = readFileSync(file, "utf8");
      const allowlisted = LAUNCHER_ALLOWLIST.get(rel) ?? new Set();

      for (const rule of EXECUTION_RULES) {
        if (rule.test(text) && !allowlisted.has(rule.id)) {
          findings.push({
            package: pkg,
            file: rel,
            rule: rule.id,
            detail: "forbidden execution/private surface",
          });
        }
      }
      for (const rule of NETWORK_RULES) {
        if (rule.test(text)) {
          findings.push({
            package: pkg,
            file: rel,
            rule: rule.id,
            detail: "direct network access; use the injected MCP stdio transport",
          });
        }
      }
      if (SERVER_RULE.test(text)) {
        findings.push({
          package: pkg,
          file: rel,
          rule: "second_controller",
          detail: "no RPH package may open a listening server (one Controller only)",
        });
      }
      if (MCP_SERVE_RULE.test(text) && !allowlisted.has("second_controller_launch")) {
        findings.push({
          package: pkg,
          file: rel,
          rule: "second_controller_launch",
          detail: "only packages/dsh-bundle/src/mcp-stdio-launch.ts may spell the mcp serve argv",
        });
      }

      const modelMarkers = EVIDENCE_MODEL_MARKERS.filter((marker) => marker.test(text));
      if (modelMarkers.length > 0 && !EVIDENCE_MODEL_ALLOWLIST.has(rel)) {
        findings.push({
          package: pkg,
          file: rel,
          rule: "duplicate_evidence_model",
          detail: `declares ${modelMarkers.map((marker) => marker.id).join(", ")}; only packages/research/src/evidence.ts may own the evidence model`,
        });
      }
      if (pkg === "bridge") {
        for (const rule of BRIDGE_MODEL_REDECLARATION) {
          if (rule.test(text)) {
            findings.push({
              package: pkg,
              file: rel,
              rule: rule.id,
              detail: "bridge parses and attributes the research evidence model; it never redeclares it",
            });
          }
        }
      }

      for (const specifier of importSpecifiers(text)) {
        if (specifier.startsWith("@deepseek-ai/dsh-")) {
          const allowed = pkg === "dsh-bundle" && specifier === "@deepseek-ai/dsh-mcp-client";
          if (!allowed) {
            findings.push({
              package: pkg,
              file: rel,
              rule: "dsh_private_import",
              detail: `import ${specifier}`,
            });
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

  return { findings, scanned, launchers: [...LAUNCHER_ALLOWLIST.keys()] };
}

function main() {
  const root = fileURLToPath(new URL("..", import.meta.url));
  const { findings, scanned, launchers } = checkBoundaries(root);

  console.log("RPH boundary matrix");
  console.log(`  packages: ${PACKAGES.join(", ")}`);
  console.log(`  scanned files: ${scanned.length}`);
  for (const pkg of PACKAGES) {
    const own = scanned.filter((file) => file.startsWith(`packages/${pkg}/`));
    console.log(
      `  ${pkg}: ${own.length} file(s), imports -> ${[...ALLOWED_IMPORTS[pkg]].join(", ")}`,
    );
  }
  console.log(`  launcher allowlist: ${launchers.join(", ")} (process + mcp serve argv only)`);
  console.log("  forbidden: child_process, spawn/exec/execFile, ssh2, node:net/http(s)/tls/dgram,");
  console.log("             fetch, undici, ws/WebSocket, createServer/listen (second Controller),");
  console.log("             a second mcp serve argv, DSH private source paths, platform store imports,");
  console.log("             wrong-direction imports,");
  console.log("             duplicate evidence models (only packages/research/src/evidence.ts),");
  console.log("             bridge re-declaring the evidence model or its decision policy");

  if (findings.length > 0) {
    console.error("\nBOUNDARY CHECK FAILED");
    for (const finding of findings) {
      console.error(`  - [${finding.rule}] ${finding.file}: ${finding.detail}`);
    }
    process.exit(1);
  }

  console.log("\nBOUNDARY CHECK PASS");
}

/* c8 ignore next 3 -- direct-execution shim; tests call checkBoundaries() */
if (process.argv[1] !== undefined && import.meta.url === `file://${process.argv[1]}`) {
  main();
}
