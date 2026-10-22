#!/usr/bin/env node
/**
 * Offline proof for the single Supervisor state machine (T-29 / C20a).
 *
 * Fixture-driven only: no Controller, model, DSH, QC or LSB process. Every
 * case supplies the EvidenceReport and the four skill contracts directly and
 * asserts the resulting stop reason. The script writes:
 *
 *   run-log.json         one entry per fixture case (phases, stop reason, ...)
 *   assertion-table.json one row per named assertion
 *   summary.json         aggregate verdict and the zero-live-call statement
 *
 * Usage:
 *   node rph/scripts/research-loop-offline-proof.mjs --out <dir> [--run-id <id>]
 */

import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import {
  EVIDENCE_REPORT_SCHEMA,
  runInterpreter,
  runPlanner,
  runReviewer,
  runSupervisor,
  runSynthesizer,
} from "../packages/dsh-bundle/dist/skills/index.js";

const DIGEST = `sha256:${"a".repeat(64)}`;
const PROJECT = "proj-diels-alder";
const RESEARCH = "res-diels-alder";
const TASK = {
  project_id: PROJECT,
  job_id: "20261023_001_Optimize",
  attempt: 1,
  release_id: "release-1",
};

function args(argv) {
  const out = { out: null, runId: null };
  for (let index = 0; index < argv.length; index += 1) {
    if (argv[index] === "--out") out.out = argv[index + 1];
    if (argv[index] === "--run-id") out.runId = argv[index + 1];
  }
  if (out.out === null) throw new Error("--out is required");
  return out;
}

function budget(overrides = {}) {
  return {
    model_requests: 64,
    input_tokens: 100000,
    output_tokens: 50000,
    task_refs: 8,
    recipe_steps: 8,
    qc_invocations: 8,
    wall_seconds: 72000,
    queue_seconds: 7200,
    ...overrides,
  };
}

function makeReport(overrides = {}) {
  return {
    schema: EVIDENCE_REPORT_SCHEMA,
    report_id: "report-r0",
    project_id: PROJECT,
    research_id: RESEARCH,
    round: 0,
    task_ref: TASK,
    status: "complete",
    decision: "accept",
    claims: [
      {
        claim_id: "claim-endo-lower",
        statement: "The endo product is lower in energy.",
        status: "supported",
        evidence_refs: ["ev-1"],
        reason: null,
      },
    ],
    evidence_refs: ["ev-1"],
    terminal_event_key: "job.completed:0",
    ...overrides,
  };
}

function baseInput(overrides = {}) {
  return {
    project_id: PROJECT,
    research_id: RESEARCH,
    question: "Which Diels-Alder product is preferred?",
    question_ref: "question-diels-alder",
    constraints: ["no_ts_seed"],
    available_inputs: ["diene", "dienophile"],
    recipe_digest: DIGEST,
    initial_input: {
      input_id: "reactant-complex",
      sha256: DIGEST,
      format: "xyz",
      role: "reactant_complex",
    },
    limits: { max_rounds: 3, budget: budget() },
    plan_definition_digest: () => DIGEST,
    ...overrides,
  };
}

const DEFAULT_SKILLS = {
  interpret: async (input) => runInterpreter(input),
  synthesize: async (input) => runSynthesizer(input),
  review: async (input) => runReviewer(input),
  plan: async (input) => runPlanner(input),
};

function countingSkills() {
  const state = { calls: 0, duringWait: 0 };
  const wrap = (fn) => async (input) => {
    state.calls += 1;
    return fn(input);
  };
  return {
    state,
    skills: {
      interpret: wrap(runInterpreter),
      synthesize: wrap(runSynthesizer),
      review: wrap(runReviewer),
      plan: wrap(runPlanner),
    },
  };
}

function ports(options = {}) {
  const log = {
    submits: 0,
    preflights: 0,
    admissions: 0,
    approvals: 0,
    waits: 0,
    persisted: [],
  };
  const reports = options.reports ?? [makeReport()];
  const waitEvents = options.waitEvents;
  return {
    log,
    ports: {
      preflight: async () => {
        log.preflights += 1;
        return {
          ok: options.preflightOk !== false,
          reason: options.preflightOk === false ? "scientific_preflight_failed" : "ok",
          checks: ["method_defined", "inputs_complete"],
        };
      },
      admission: async () => {
        log.admissions += 1;
        return {
          ok: true,
          reason: "ok",
          approval_ref: "approval-1",
        };
      },
      approve: async () => {
        log.approvals += 1;
        return { approved: true, approval_ref: "approval-1", reason: "ok" };
      },
      submit: async (request) => {
        log.submits += 1;
        return {
          ok: true,
          task_ref: { ...TASK, attempt: request.round + 1 },
          reason: "submitted",
        };
      },
      wait: async () => {
        const index = log.waits;
        log.waits += 1;
        const before = options.skillState?.calls ?? 0;
        const selected = waitEvents?.[index] ?? [
          {
            event_key: `job.completed:${index}`,
            type: "job.completed",
            task_ref: { ...TASK, attempt: index + 1 },
            report: reports[Math.min(index, reports.length - 1)] ?? null,
          },
        ];
        if (options.skillState !== undefined) {
          options.skillState.duringWait += options.skillState.calls - before;
        }
        return { events: selected, cursor: `cursor-${index}` };
      },
      persistTaskRef: async (ref) => {
        log.persisted.push(ref);
      },
      persistReport: async () => undefined,
    },
  };
}

function terminated(result) {
  return result.phases.at(-1)?.phase === "stop";
}

const cases = [];

async function record(name, expected, run) {
  const { result, log, extra = {} } = await run();
  const assertions = [
    {
      name: `${name}.terminal_stop_phase`,
      status: terminated(result) ? "PASS" : "FAIL",
      detail: result.phases.at(-1)?.phase ?? "missing",
    },
    {
      name: `${name}.stop_reason`,
      status: result.stop_reason === expected ? "PASS" : "FAIL",
      detail: `expected=${expected} actual=${result.stop_reason}`,
    },
  ];
  for (const [label, ok, detail] of extra.assertions ?? []) {
    assertions.push({
      name: `${name}.${label}`,
      status: ok ? "PASS" : "FAIL",
      detail: String(detail),
    });
  }
  cases.push({
    case: name,
    stop_reason: result.stop_reason,
    status: result.status,
    submits: result.submits,
    rounds_completed: result.rounds_completed,
    model_invocations: result.model_invocations,
    wait_invocations: result.wait_invocations,
    duplicate_terminal_events: result.duplicate_terminal_events,
    task_refs: result.task_refs,
    report_ids: result.reports.map((item) => item.report_id),
    phases: result.phases,
    ports: log,
    assertions,
  });
}

async function main() {
  const options = args(process.argv.slice(2));
  const runId = options.runId ?? "offline-t29-r1";
  mkdirSync(options.out, { recursive: true });

  await record("happy_path", "accepted", async () => {
    const { ports: p, log } = ports();
    const result = await runSupervisor(baseInput(), p);
    return {
      result,
      log,
      extra: {
        assertions: [
          ["submits_one", result.submits === 1, result.submits],
          [
            "phase_order",
            result.phases.map((item) => item.phase).join(",") ===
              "understand,hypothesize,critique,plan,approve,wait,evaluate,stop",
            result.phases.map((item) => item.phase).join(","),
          ],
          ["task_ref_persisted", log.persisted.length === 1, log.persisted.length],
        ],
      },
    };
  });

  await record("no_evidence", "no_evidence", async () => {
    const empty = makeReport({
      status: "missing",
      decision: "revise",
      claims: [],
      evidence_refs: [],
    });
    const { ports: p, log } = ports({ reports: [empty] });
    const result = await runSupervisor(baseInput(), p);
    return {
      result,
      log,
      extra: {
        assertions: [
          ["no_second_submit", result.submits === 1, result.submits],
          ["no_second_round", log.waits === 1, log.waits],
        ],
      },
    };
  });

  await record("duplicate_terminal", "duplicate_terminal", async () => {
    const revise = makeReport({ decision: "revise" });
    const event = {
      event_key: "job.completed:same",
      type: "job.completed",
      task_ref: TASK,
      report: revise,
    };
    const { ports: p, log } = ports({
      waitEvents: [[event], [event]],
      reports: [revise],
    });
    const result = await runSupervisor(
      baseInput({ limits: { max_rounds: 4, budget: budget() } }),
      p,
    );
    return {
      result,
      log,
      extra: {
        assertions: [
          ["no_extra_submit", result.submits === 2, result.submits],
          [
            "duplicate_counted",
            result.duplicate_terminal_events === 1,
            result.duplicate_terminal_events,
          ],
        ],
      },
    };
  });

  await record("round_cap", "round_cap", async () => {
    const events = [0, 1, 2].map((index) => [
      {
        event_key: `job.completed:${index}`,
        type: "job.completed",
        task_ref: { ...TASK, attempt: index + 1 },
        report: makeReport({
          report_id: `report-r${index}`,
          round: index,
          decision: "revise",
        }),
      },
    ]);
    const { ports: p, log } = ports({ waitEvents: events });
    const result = await runSupervisor(
      baseInput({ limits: { max_rounds: 2, budget: budget() } }),
      p,
    );
    return {
      result,
      log,
      extra: {
        assertions: [
          ["cap_submits", result.submits === 2, result.submits],
          ["cap_rounds", result.rounds_completed === 2, result.rounds_completed],
        ],
      },
    };
  });

  await record("preflight_failed", "preflight_failed", async () => {
    const { ports: p, log } = ports({ preflightOk: false });
    const result = await runSupervisor(baseInput(), p);
    return {
      result,
      log,
      extra: {
        assertions: [
          ["no_submit", result.submits === 0, result.submits],
          ["no_admission", log.admissions === 0, log.admissions],
        ],
      },
    };
  });

  await record("advice_cannot_override_refuted", "refuted", async () => {
    const refuted = makeReport({
      decision: "accept",
      claims: [
        {
          claim_id: "claim-endo-lower",
          statement: "The endo product is lower in energy.",
          status: "refuted",
          evidence_refs: ["ev-1"],
          reason: "the exo product is lower",
        },
      ],
    });
    const { ports: p, log } = ports({ reports: [refuted] });
    const result = await runSupervisor(baseInput(), p, {
      ...DEFAULT_SKILLS,
      synthesize: async () => ({
        schema: "acp2.skill.synthesizer.output.v1",
        skill: "synthesizer",
        skill_version: "1.0.0",
        hypothesis: "keep trying",
        rationale: "the model wants another expensive round",
        advice: "continue",
        expected_claims: [],
        evidence_refs: [],
      }),
    });
    return {
      result,
      log,
      extra: {
        assertions: [
          ["advice_recorded", result.last_advice === "continue", result.last_advice],
          ["single_submit", result.submits === 1, result.submits],
        ],
      },
    };
  });

  await record("wait_never_calls_model", "accepted", async () => {
    const { skills, state } = countingSkills();
    const { ports: p, log } = ports({ skillState: state });
    const result = await runSupervisor(baseInput(), p, skills);
    return {
      result,
      log,
      extra: {
        assertions: [
          ["no_skill_during_wait", state.duringWait === 0, state.duringWait],
          ["model_invocations", state.calls === 5, state.calls],
          ["wait_called", result.wait_invocations === 1, result.wait_invocations],
        ],
      },
    };
  });

  const assertions = cases.flatMap((item) => item.assertions);
  const failed = assertions.filter((item) => item.status !== "PASS");
  const generatedAt = new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
  const summary = {
    schema: "acp2.experience.research-loop.offline-proof.v1",
    schema_version: "1.0.0",
    run_id: runId,
    generated_at: generatedAt,
    presenter: "rph/packages/dsh-bundle/skills/supervisor.ts",
    live_model_calls: 0,
    controller_calls: 0,
    cases: cases.map((item) => item.case),
    assertions: assertions.length,
    failed: failed.length,
    all_passed: failed.length === 0,
  };

  writeFileSync(
    join(options.out, "run-log.json"),
    `${JSON.stringify({ schema: summary.schema, run_id: runId, cases }, null, 2)}\n`,
    "utf8",
  );
  writeFileSync(
    join(options.out, "assertion-table.json"),
    `${JSON.stringify(assertions, null, 2)}\n`,
    "utf8",
  );
  writeFileSync(
    join(options.out, "summary.json"),
    `${JSON.stringify(summary, null, 2)}\n`,
    "utf8",
  );

  if (!summary.all_passed) {
    console.error(JSON.stringify(summary, null, 2));
    process.exit(1);
  }
  console.log(
    `offline proof PASS: ${cases.length} cases, ${assertions.length} assertions -> ${options.out}`,
  );
}

main().catch((error) => {
  console.error(error instanceof Error ? error.stack : String(error));
  process.exit(1);
});
