/**
 * M3-02a — the CLI composition root, the command surface and the fail-fast
 * matrix.
 *
 * Proven here:
 * - the version is read from the CLI manifest (no second `0.0.0` constant) and
 *   the four packages share one product version;
 * - `research`/`resume`/`doctor` are real commands with a real `bin`;
 * - `--mode demo` starts the packaged Supervisor end to end, is labelled
 *   `evidence_kind: fixture`, and refuses a real Controller;
 * - test skills outside the demo and a demo outside `dev` are refused;
 * - a product/acceptance composition lists **every** missing prerequisite in one
 *   non-zero exit (model skills, model gateway, research writer, P1/P2 surface);
 * - the same command runs one real Supervisor round when the product parts are
 *   injected, through a real negotiated session (fake transport, no process);
 * - `doctor` is three-state (pass/fail/skip) in both static and `--connect`
 *   mode and never prints a credential.
 */

import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import {
  FIXTURE_SUPERVISOR_SKILLS,
  createMemoryResearchWriter,
  runFixturePlanner,
  type ScienceSeed,
  type SupervisorSkills,
} from "@rph/dsh-bundle";
import { CLI_VERSION, EXIT_INCOMPLETE, EXIT_OK, main, parseArgs, CliUsageError, usage } from "@rph/cli";

import { P1_TOOL_NAMES, parsePlatformTaskRef } from "@rph/bridge";

import { TEST_OPTIONS } from "./harness.js";
import {
  ALL_P2_TOOL_NAMES,
  FAKE_REPORT_DIGEST,
  FAKE_REPORT_ID,
  captureIo,
  fakeEvidenceView,
  fakeServer,
  fakeTaskRefPayload,
  lastJson,
} from "./m3-02-harness.js";

const RPH_ROOT = fileURLToPath(new URL("../..", import.meta.url));
const DIGEST = "sha256:" + "a".repeat(64);

/** A complete acceptance environment (no host path is hard-coded in the code). */
const ACCEPTANCE_ENV = {
  ACP2_CONTROLLER_URL: "http://127.0.0.1:18765",
  RPH_INTERPRETER: "python3.11",
  RPH_WORKING_DIRECTORY: "/srv/rph",
  ACP_RUN_ROOT: "/srv/run-root",
  ACP_CONFIG: "/srv/cccp.yaml",
  RPH_PROJECT: "proj-1",
  RPH_PERMISSION_SCOPE: "read",
  RPH_ARTIFACT_EVIDENCE_PATH: "/srv/evidence",
  RPH_RESEARCH_WRITER: "research-store",
  RPH_MODEL_REQUESTS: "64",
  RPH_MODEL_PROVIDER: "local",
  RPH_MODEL_ID: "test-model",
} as const;

const SCIENCE_SEED: ScienceSeed = {
  method: "r2SCAN-3c",
  mapping: "identity",
  charge: 0,
  multiplicity: 1,
  electronic_state: {
    charge: 0,
    multiplicity: 1,
    state_label: "singlet",
    spin_polarization: 0,
  },
  structure_provenance: [
    {
      input_id: "input-1",
      sha256: DIGEST,
      format: "smiles",
      role: "minimum",
    },
  ],
  constraints: [],
};

function scienceFile(): string {
  const dir = mkdtempSync(join(tmpdir(), "rph-m3-02-"));
  const path = join(dir, "science.json");
  writeFileSync(
    path,
    JSON.stringify({
      science_seed: SCIENCE_SEED,
      initial_input: {
        input_id: "input-1",
        sha256: DIGEST,
        format: "smiles",
        role: "minimum",
      },
    }),
  );
  return path;
}

const DURABLE_WRITER = (): { kind: string; persistTaskRef: () => Promise<void>; persistReport: () => Promise<void> } => ({
  kind: "research-store",
  persistTaskRef: async () => {},
  persistReport: async () => {},
});

// ---------------------------------------------------------------------------
// Version: one source, one product version
// ---------------------------------------------------------------------------

test("cli version comes from the cli manifest only", () => {
  const manifest = JSON.parse(
    readFileSync(join(RPH_ROOT, "packages", "cli", "package.json"), "utf8"),
  ) as { version: string; bin: Record<string, string> };
  assert.equal(CLI_VERSION, manifest.version);
  assert.equal(manifest.bin.rph, "./dist/main.js");
  const roots = ["cli", "dsh-bundle", "bridge", "research"].flatMap((name) => [
    join(RPH_ROOT, "packages", name, "src"),
    join(RPH_ROOT, "packages", name, "skills"),
  ]);
  for (const root of roots) {
    let files: string[];
    try {
      files = readdirSync(root);
    } catch {
      continue;
    }
    for (const file of files) {
      if (!file.endsWith(".ts")) continue;
      const text = readFileSync(join(root, file), "utf8");
      assert.equal(text.includes('"0.0.0"'), false, `${root}/${file} keeps a version constant`);
    }
  }
});

test("all four packages share one product version", () => {
  const versions = ["research", "bridge", "dsh-bundle", "cli"].map((name) => {
    const manifest = JSON.parse(
      readFileSync(join(RPH_ROOT, "packages", name, "package.json"), "utf8"),
    ) as { version: string };
    return manifest.version;
  });
  assert.deepEqual(new Set(versions).size, 1, `versions drifted: ${versions.join(", ")}`);
  assert.equal(versions[0], CLI_VERSION);
});

// ---------------------------------------------------------------------------
// Parser and command surface
// ---------------------------------------------------------------------------

test("parser recognises research, resume and doctor", { ...TEST_OPTIONS }, () => {
  assert.deepEqual(parseArgs(["research"]).command, "research");
  assert.deepEqual(parseArgs(["resume"]).command, "resume");
  assert.deepEqual(parseArgs(["doctor"]).command, "doctor");
  const research = parseArgs([
    "research",
    "--mode",
    "demo",
    "--skills",
    "test",
    "--environment",
    "dev",
    "--question",
    "why",
    "--json",
  ]);
  assert.equal(research.mode, "demo");
  assert.equal(research.skills, "test");
  assert.equal(research.environment, "dev");
  assert.equal(research.question, "why");
  assert.equal(research.json, true);
  const resume = parseArgs([
    "resume",
    "--proposal",
    "prop-1",
    "--plan-digest",
    DIGEST,
    "--task",
    "job-1",
    "--cursor",
    "c1",
  ]);
  assert.equal(resume.proposal, "prop-1");
  assert.equal(resume.planDigest, DIGEST);
  assert.equal(resume.task, "job-1");
  assert.equal(resume.cursor, "c1");
  assert.equal(parseArgs(["doctor", "--connect"]).connect, true);
  assert.equal(parseArgs(["--profile", "web"]).profile, "web");
  assert.throws(() => parseArgs(["research", "--mode", "prod"]), CliUsageError);
  assert.throws(() => parseArgs(["research", "--environment", "prod"]), CliUsageError);
  assert.throws(() => parseArgs(["research", "--skills", "fixture"]), CliUsageError);
  assert.throws(() => parseArgs(["research", "--max-rounds", "x"]), CliUsageError);
  const text = usage();
  for (const marker of ["rph research", "rph resume", "rph doctor", "--profile <web|headless>"]) {
    assert.equal(text.includes(marker), true, `usage is missing ${marker}`);
  }
});

test("help and version print without side effects", { ...TEST_OPTIONS }, async () => {
  const help = captureIo({});
  assert.equal(await main(["--help"], {}, help), EXIT_OK);
  assert.equal(help.out.join("\n").includes("rph research"), true);
  const version = captureIo({});
  assert.equal(await main(["--version"], {}, version), EXIT_OK);
  assert.equal(version.out.join("\n"), `rph ${CLI_VERSION}`);
});

// ---------------------------------------------------------------------------
// Demo: labelled fixture, never against a Controller
// ---------------------------------------------------------------------------

test("demo starts the packaged Supervisor and is labelled as a fixture", { ...TEST_OPTIONS }, async () => {
  const io = captureIo({});
  const code = await main(
    ["research", "--mode", "demo", "--skills", "test", "--json"],
    {},
    io,
  );
  assert.equal(code, EXIT_OK);
  const json = lastJson(io);
  assert.equal(json["mode"], "demo");
  assert.equal(json["demo"], true);
  assert.equal(json["evidence_kind"], "fixture");
  const result = json["result"] as Record<string, unknown>;
  assert.equal(result["status"], "completed");
  assert.equal(result["stop_reason"], "accepted");
  assert.equal(result["submits"], 1);
});

test("demo human output carries the fixture disclaimer", { ...TEST_OPTIONS }, async () => {
  const io = captureIo({});
  const code = await main(["research", "--mode", "demo", "--skills", "test"], {}, io);
  assert.equal(code, EXIT_OK);
  assert.equal(io.out.join("\n").includes("DEMO (fixture) — not a scientific result"), true);
});

test("a demo refuses a real Controller", { ...TEST_OPTIONS }, async () => {
  const io = captureIo({ ACP2_CONTROLLER_URL: "http://127.0.0.1:18765" });
  const code = await main(["research", "--mode", "demo", "--skills", "test", "--json"], {}, io);
  assert.equal(code, EXIT_INCOMPLETE);
  const json = lastJson(io);
  const refusals = json["refusals"] as Array<{ code: string }>;
  assert.equal(refusals.some((entry) => entry.code === "demo_refuses_controller"), true);
});

test("a demo is refused outside dev", { ...TEST_OPTIONS }, async () => {
  const io = captureIo({});
  const code = await main(
    ["research", "--mode", "demo", "--skills", "test", "--environment", "acceptance", "--json"],
    {},
    io,
  );
  assert.equal(code, EXIT_INCOMPLETE);
  const refusals = lastJson(io)["refusals"] as Array<{ code: string }>;
  assert.equal(refusals.some((entry) => entry.code === "demo_not_allowed"), true);
});

test("test skills are demo-only", { ...TEST_OPTIONS }, async () => {
  const io = captureIo({});
  const code = await main(
    [
      "research",
      "--skills",
      "test",
      "--question",
      "q",
      "--recipe-digest",
      DIGEST,
      "--json",
    ],
    {},
    io,
  );
  assert.equal(code, EXIT_INCOMPLETE);
  const refusals = lastJson(io)["refusals"] as Array<{ code: string }>;
  assert.equal(refusals.some((entry) => entry.code === "fixture_skills_require_demo"), true);
});

// ---------------------------------------------------------------------------
// Fail-fast matrix: every missing prerequisite, one non-zero exit
// ---------------------------------------------------------------------------

test("product mode lists every missing prerequisite at once", { ...TEST_OPTIONS }, async () => {
  const io = captureIo({});
  const code = await main(
    ["research", "--question", "q", "--recipe-digest", DIGEST, "--json"],
    {},
    io,
  );
  assert.equal(code, EXIT_INCOMPLETE);
  const json = lastJson(io);
  assert.equal(json["status"], "incomplete");
  assert.equal(json["mode"], "product");
  const codes = (json["issues"] as Array<{ code: string }>).map((entry) => entry.code);
  for (const expected of [
    "model_skills_missing",
    "research_writer_missing",
    "artifact_evidence_path_missing",
    "science_seed_missing",
  ]) {
    assert.equal(codes.includes(expected), true, `missing issue ${expected} (${codes.join(",")})`);
  }
  assert.equal(codes.some((codeName) => codeName.startsWith("platform_session_")), true);
  // The strict environment reports its unresolved parameters in the same list.
  assert.equal(codes.includes("parameters_missing"), true, codes.join(","));
});

test("a P1-only platform session reports the P2 surface together with the rest", { ...TEST_OPTIONS }, async () => {
  const server = fakeServer({ negotiated: false, tools: P1_TOOL_NAMES });
  const io = captureIo(ACCEPTANCE_ENV);
  const code = await main(
    ["research", "--mode", "acceptance", "--question", "q", "--recipe-digest", DIGEST, "--json"],
    { createTransport: () => server.transport },
    io,
  );
  assert.equal(code, EXIT_INCOMPLETE);
  const codes = (lastJson(io)["issues"] as Array<{ code: string }>).map((entry) => entry.code);
  for (const expected of [
    "model_skills_missing",
    "model_gateway_missing",
    "research_writer_missing",
    "p2_tool_surface_incomplete",
  ]) {
    assert.equal(codes.includes(expected), true, `missing issue ${expected} (${codes.join(",")})`);
  }
});

test("a product run binds evidence, persists the decision and accepts", { ...TEST_OPTIONS }, async () => {
  const server = fakeServer({ negotiated: true });
  const bound = parsePlatformTaskRef(fakeTaskRefPayload("proj-1"), "proj-1");
  const view = fakeEvidenceView(bound, "demo-research", 0);
  const modelSkills: SupervisorSkills = {
    ...FIXTURE_SUPERVISOR_SKILLS,
    planner_kind: "model",
    plan: async (input) => runFixturePlanner(input),
  };
  const decisions: unknown[] = [];
  const checkpoints: string[] = [];
  const writer = {
    kind: "research-store",
    persistTaskRef: async () => {},
    persistReport: async () => {},
    persistDecision: async (record: unknown) => {
      decisions.push(record);
      return { ok: true, replayed: false, revision: decisions.length };
    },
    persistCheckpoint: async (checkpoint: { stage: string }) => {
      checkpoints.push(checkpoint.stage);
      return { ok: true, replayed: false };
    },
  };
  const io = captureIo(ACCEPTANCE_ENV);
  const code = await main(
    [
      "research",
      "--question",
      "which conformer wins",
      "--recipe-digest",
      DIGEST,
      "--science",
      scienceFile(),
      "--json",
    ],
    {
      createTransport: () => server.transport,
      parts: () => ({
        skills: modelSkills,
        writer,
        modelGateway: async () => ({
          model_requests: 0,
          receipts: [],
          source: "platform-receipt" as const,
        }),
        scienceSeed: SCIENCE_SEED,
        initialInput: {
          input_id: "input-1",
          sha256: DIGEST,
          format: "smiles",
          role: "minimum",
        },
        proposalRef: "prop-m3-02",
        submitPayload: () => ({
          workflow: "optimize",
          input: { source: "CCO", source_type: "smiles" },
          method: { name: "r2SCAN-3c" },
          resources: { cores: 1 },
          budget: { unit: "task_count", count: 1, timeout_s: 120 },
          proposal_ref: "prop-m3-02",
          proposal_digest: DIGEST,
        }),
        evidenceReader: {
          read: async () => ({ view, read_at: "2026-10-27T00:00:00Z" }),
        },
        budgetUsage: {
          read: async () => ({
            source: "saga-record" as const,
            used: {
              model_requests: 0,
              input_tokens: 0,
              output_tokens: 0,
              task_refs: 0,
              recipe_steps: 0,
              qc_invocations: 0,
              wall_seconds: 0,
              queue_seconds: 0,
            },
            receipts: ["saga:budget:1"],
          }),
        },
        adapters: {
          preflight: async () => ({ ok: true, reason: "injected", checks: ["injected"] }),
          admission: async () => ({ ok: true, reason: "injected", approval_ref: "ap_demo_fixture" }),
          // No `submit` adapter: the real bridge submit port performs the P1
          // call (verbatim intent key) against the injected transport.
          wait: async (request) => ({
            events: [
              {
                event_key: "ev-1",
                type: "job.completed",
                task_ref: request.task_ref,
                report: null,
                report_id: FAKE_REPORT_ID,
                report_digest: FAKE_REPORT_DIGEST,
              },
            ],
            cursor: "cursor-1",
          }),
        },
      }),
    },
    io,
  );
  const json = lastJson(io);
  assert.equal(json["mode"], "product");
  assert.equal(json["evidence_kind"], "live");
  assert.equal(json["demo"], false);
  assert.equal(code, EXIT_OK, JSON.stringify(json));
  const result = json["result"] as Record<string, unknown>;
  assert.equal(result["stop_reason"], "accepted");
  assert.equal(result["submits"], 1);
  assert.equal(result["bindings"], 1);
  assert.equal(result["decision"], "accept");
  assert.equal(result["resume_state"], null);
  // The decision was persisted through the writer CAS, and the run checkpointed
  // its bound task before evaluating.
  assert.equal(decisions.length, 1);
  assert.equal(checkpoints.includes("task_bound"), true);
  // The platform saw exactly one submit, carrying the persisted intent wire key.
  const submitCalls = server.sent.filter(
    (frame) =>
      frame["method"] === "tools/call" &&
      (frame["params"] as Record<string, unknown>)["name"] === "acp_submit",
  );
  assert.equal(submitCalls.length, 1);
  const submitArgs = (submitCalls[0]?.["params"] as Record<string, unknown>)["arguments"] as
    | Record<string, unknown>
    | undefined;
  assert.match(String(submitArgs?.["idempotency_key"] ?? ""), /^rph\.[0-9a-f]{64}$|^[A-Za-z0-9._:-]{8,128}$/);
});

test("a product run without an evidence binding fails the matrix, not the run", { ...TEST_OPTIONS }, async () => {
  const server = fakeServer({ negotiated: true });
  const modelSkills: SupervisorSkills = {
    ...FIXTURE_SUPERVISOR_SKILLS,
    planner_kind: "model",
    plan: async (input) => runFixturePlanner(input),
  };
  const io = captureIo(ACCEPTANCE_ENV);
  const code = await main(
    [
      "research",
      "--question",
      "q",
      "--recipe-digest",
      DIGEST,
      "--science",
      scienceFile(),
      "--json",
    ],
    {
      createTransport: () => server.transport,
      parts: () => ({
        skills: modelSkills,
        writer: DURABLE_WRITER(),
        modelGateway: async () => ({ model_requests: 0, receipts: [], source: "platform-receipt" }),
        scienceSeed: SCIENCE_SEED,
        initialInput: {
          input_id: "input-1",
          sha256: DIGEST,
          format: "smiles",
          role: "minimum",
        },
      }),
    },
    io,
  );
  assert.equal(code, EXIT_INCOMPLETE);
  const codes = (lastJson(io)["issues"] as Array<{ code: string }>).map((entry) => entry.code);
  for (const expected of [
    "evidence_reader_missing",
    "decision_writer_missing",
    "checkpoint_writer_missing",
    "budget_usage_source_missing",
  ]) {
    assert.equal(codes.includes(expected), true, `missing issue ${expected} (${codes.join(",")})`);
  }
  // Nothing was submitted: an incomplete binding is a pre-flight refusal.
  assert.equal(
    server.sent.some(
      (frame) =>
        frame["method"] === "tools/call" &&
        (frame["params"] as Record<string, unknown>)["name"] === "acp_submit",
    ),
    false,
  );
});

// ---------------------------------------------------------------------------
// doctor
// ---------------------------------------------------------------------------

test("doctor static mode is three-state and never checks a session", { ...TEST_OPTIONS }, async () => {
  const io = captureIo({});
  const code = await main(["doctor", "--json"], {}, io);
  assert.equal(code, EXIT_OK);
  const report = lastJson(io);
  assert.equal(report["schema"], "acp2.rph.doctor.v1");
  assert.equal(report["mode"], "static");
  assert.equal(report["status"], "ok");
  const checks = report["checks"] as Array<{ id: string; status: string }>;
  const byId = new Map(checks.map((entry) => [entry.id, entry.status]));
  assert.equal(byId.get("paths"), "pass");
  assert.equal(byId.get("secrets"), "pass");
  for (const connectOnly of ["handshake", "permissions", "extensions"]) {
    assert.equal(byId.get(connectOnly), "skip", `${connectOnly} must be skip, never pass`);
  }
});

test("doctor fails a strict environment that is missing required parameters", { ...TEST_OPTIONS }, async () => {
  const io = captureIo({});
  const code = await main(["doctor", "--environment", "release", "--json"], {}, io);
  assert.equal(code, 1);
  const report = lastJson(io);
  assert.equal(report["status"], "failed");
  const paths = (report["checks"] as Array<{ id: string; status: string; detail: string }>).find(
    (entry) => entry.id === "paths",
  );
  assert.equal(paths?.status, "fail");
  assert.match(paths?.detail ?? "", /RPH_INTERPRETER/);
  assert.match(paths?.detail ?? "", /ACP_RUN_ROOT/);
});

test("doctor --connect reports the real handshake and extension answer", { ...TEST_OPTIONS }, async () => {
  const server = fakeServer({ negotiated: true });
  const io = captureIo(ACCEPTANCE_ENV);
  const code = await main(
    ["doctor", "--connect", "--environment", "acceptance", "--json"],
    {
      createTransport: () => server.transport,
      parts: () => ({
        skills: { ...FIXTURE_SUPERVISOR_SKILLS, planner_kind: "model" },
        writer: DURABLE_WRITER(),
        modelGateway: async () => ({ model_requests: 0, receipts: [], source: "platform-receipt" }),
        scienceSeed: SCIENCE_SEED,
      }),
    },
    io,
  );
  const report = lastJson(io);
  assert.equal(report["mode"], "connect");
  const checks = new Map(
    (report["checks"] as Array<{ id: string; status: string }>).map((entry) => [
      entry.id,
      entry.status,
    ]),
  );
  assert.equal(checks.get("handshake"), "pass");
  assert.equal(checks.get("extensions"), "pass");
  assert.equal(checks.get("writer"), "pass");
  assert.equal(report["status"], "failed");
  assert.ok((report["readiness"] as Array<{id: string; status: string}>).some((fact) => fact.id === "product_tools" && fact.status === "fail"));
  assert.equal(code, 1);
});

test("doctor masks the controller credential", { ...TEST_OPTIONS }, async () => {
  const secret = "sk-livesecret0123456789abcdef";
  const io = captureIo({ RPH_CONTROLLER_API_KEY: secret });
  await main(["doctor", "--json"], {}, io);
  const text = io.out.join("\n");
  assert.equal(text.includes(secret), false);
  assert.equal(text.includes("<redacted:env>"), true);
});

test("doctor reports a missing session as failed, never as pass", { ...TEST_OPTIONS }, async () => {
  const io = captureIo({ ACP2_CONTROLLER_URL: "http://127.0.0.1:1", RPH_INTERPRETER: "python3.11" });
  const code = await main(
    ["doctor", "--connect", "--json"],
    {
      createTransport: () => ({
        send() {
          throw new Error("not reachable");
        },
        onLine() {},
        onError() {},
        onClose() {},
        close() {},
      }),
    },
    io,
  );
  assert.equal(code, 1);
  const checks = new Map(
    (lastJson(io)["checks"] as Array<{ id: string; status: string }>).map((entry) => [
      entry.id,
      entry.status,
    ]),
  );
  assert.equal(checks.get("handshake"), "fail");
});

// ---------------------------------------------------------------------------
// resume
// ---------------------------------------------------------------------------

test("resume refuses a demo and never fabricates a saga", { ...TEST_OPTIONS }, async () => {
  const io = captureIo({});
  const code = await main(
    ["resume", "--mode", "demo", "--proposal", "prop-1", "--plan-digest", DIGEST, "--json"],
    {},
    io,
  );
  assert.equal(code, EXIT_INCOMPLETE);
  assert.equal(lastJson(io)["code"], "demo_resume_refused");
});

test("resume requires the proposal and plan digest", { ...TEST_OPTIONS }, async () => {
  const io = captureIo({});
  const code = await main(["resume", "--json"], {}, io);
  assert.equal(code, 2);
  assert.match(io.err.join("\n"), /resume requires --proposal/);
});

test("resume reaches the platform resume operation through the session", { ...TEST_OPTIONS }, async () => {
  const server = fakeServer({ negotiated: true });
  const io = captureIo({
    ACP2_CONTROLLER_URL: "http://127.0.0.1:18765",
    RPH_INTERPRETER: "python3.11",
  });
  const code = await main(
    ["resume", "--proposal", "prop-1", "--plan-digest", DIGEST, "--json"],
    { createTransport: () => server.transport },
    io,
  );
  const json = lastJson(io);
  assert.equal(json["status"], "resumed");
  assert.equal(code, EXIT_OK);
  const call = server.sent.find(
    (frame) =>
      frame["method"] === "tools/call" &&
      (frame["params"] as Record<string, unknown>)["name"] === "acp_research_resume_v1",
  );
  assert.notEqual(call, undefined, "the resume tool call never reached the platform");
});

test("a product run refuses the in-memory fixture writer", { ...TEST_OPTIONS }, async () => {
  const server = fakeServer({ negotiated: true });
  const writer = createMemoryResearchWriter();
  assert.equal(writer.kind, "memory-fixture");
  const io = captureIo(ACCEPTANCE_ENV);
  const code = await main(
    [
      "research",
      "--mode",
      "acceptance",
      "--question",
      "q",
      "--recipe-digest",
      DIGEST,
      "--json",
    ],
    {
      createTransport: () => server.transport,
      parts: () => ({
        writer,
        skills: { ...FIXTURE_SUPERVISOR_SKILLS, planner_kind: "model" },
        scienceSeed: SCIENCE_SEED,
        initialInput: {
          input_id: "input-1",
          sha256: DIGEST,
          format: "smiles",
          role: "minimum",
        },
      }),
    },
    io,
  );
  assert.equal(code, EXIT_INCOMPLETE);
  const codes = (lastJson(io)["issues"] as Array<{ code: string }>).map((entry) => entry.code);
  assert.equal(codes.includes("research_writer_fixture"), true, codes.join(","));
});

test("the cli package sources contain no host path literal", () => {
  for (const file of readdirSync(join(RPH_ROOT, "packages", "cli", "src"))) {
    if (!file.endsWith(".ts")) continue;
    const text = readFileSync(join(RPH_ROOT, "packages", "cli", "src", file), "utf8");
    for (const forbidden of ["/ACPServer", "acp2-runs", "acp2-development"]) {
      assert.equal(text.includes(forbidden), false, `${dirname(file)}/${file} contains ${forbidden}`);
    }
  }
});
