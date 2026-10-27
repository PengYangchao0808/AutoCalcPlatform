/**
 * Explicit demo runtime (M3-02).
 *
 * A demo is a **labelled fixture**: deterministic skills, in-memory ports, a
 * fixture EvidenceReport and `evidence_kind: "fixture"`. It exists so the
 * packaged Supervisor can be started end to end without a Controller, model or
 * QC, and it can never be confused with a product run:
 *
 *   - the CLI requires `--mode demo` (the environment must also allow it);
 *   - the CLI refuses a demo when a real Controller URL was supplied;
 *   - every report carries `demo: true` and `evidence_kind: "fixture"`;
 *   - `composeResearchRuntime` refuses the demo ports in product mode.
 */

import type { EvidenceReport, TaskRef } from "../skills/models.js";
import type {
  SubmitRequest,
  SupervisorPorts,
  WaitEvent,
  WaitRequest,
} from "../skills/supervisor.js";

/** The only evidence kind a demo can produce. */
export const DEMO_EVIDENCE_KIND = "fixture" as const;

/** Marker carried by every demo output. */
export const DEMO_MARKER = "demo-fixture" as const;

export interface DemoContext {
  readonly project_id: string;
  readonly research_id: string;
  readonly question: string;
}

function demoTaskRef(projectId: string): TaskRef {
  return Object.freeze({
    project_id: projectId,
    job_id: "demo-job-1",
    attempt: 1,
    release_id: "demo-release",
  });
}

/** The deterministic demo report (a fixture, never a scientific result). */
export function buildDemoReport(
  context: DemoContext,
  taskRef: TaskRef,
  round: number,
): EvidenceReport {
  return Object.freeze({
    schema: "acp2.evidence.report.v1" as const,
    report_id: `demo-report-r${round}`,
    project_id: context.project_id,
    research_id: context.research_id,
    round,
    task_ref: taskRef,
    status: "complete" as const,
    decision: "accept" as const,
    claims: Object.freeze([
      Object.freeze({
        claim_id: "demo-claim-1",
        statement: `demo fixture for: ${context.question}`,
        status: "supported" as const,
        evidence_refs: Object.freeze(["demo:evidence-1"]),
        reason: null,
      }),
    ]),
    evidence_refs: Object.freeze(["demo:evidence-1"]),
    terminal_event_key: `demo-terminal-r${round}`,
  });
}

/**
 * In-memory supervisor ports for the demo.
 *
 * They are deterministic and side-effect free: no transport, no process, no
 * platform call. Every result is marked `demo-fixture` so a log reader can tell
 * it apart from a platform fact.
 */
export function createDemoPorts(context: DemoContext): SupervisorPorts {
  return Object.freeze({
    async preflight() {
      return Object.freeze({
        ok: true,
        reason: DEMO_MARKER,
        checks: Object.freeze([DEMO_MARKER]),
      });
    },
    async admission() {
      return Object.freeze({
        ok: true,
        reason: DEMO_MARKER,
        approval_ref: "ap_demo_fixture",
      });
    },
    async approve() {
      return Object.freeze({
        approved: true,
        approval_ref: "ap_demo_fixture",
        reason: DEMO_MARKER,
      });
    },
    async submit(request: SubmitRequest) {
      return Object.freeze({
        ok: true,
        task_ref: demoTaskRef(request.project_id),
        reason: DEMO_MARKER,
      });
    },
    async wait(request: WaitRequest) {
      const event: WaitEvent = Object.freeze({
        event_key: `demo-terminal-r${request.round}`,
        type: "job.completed",
        task_ref: request.task_ref,
        report: buildDemoReport(context, request.task_ref, request.round),
      });
      return Object.freeze({ events: Object.freeze([event]), cursor: null });
    },
  });
}
