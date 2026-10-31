// Real compiled RPH modules. Injected deterministic ports isolate science effects.
import { readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';
import { buildToolArguments, assertOperationAllowed, BRIDGE_EXTENSION_VERSION } from '../../rph/packages/bridge/dist/index.js';
import { runSupervisor, validateEvidenceReport } from '../../rph/packages/dsh-bundle/dist/src/index.js';

const digest = `sha256:${'a'.repeat(64)}`;
const task = { project_id: 'audit-project', job_id: 'audit-job', attempt: 1, release_id: 'release-a' };
const budget = { model_requests: 64, input_tokens: 100000, output_tokens: 50000, task_refs: 8, recipe_steps: 8, qc_invocations: 8, wall_seconds: 72000, queue_seconds: 7200 };
const input = { project_id: task.project_id, research_id: 'audit-research', question: 'Does the supplied charged doublet retain its spin?', question_ref: null, constraints: ['charge=1', 'multiplicity=2'], available_inputs: ['input-a'], recipe_digest: digest, initial_input: { input_id: 'input-a', sha256: digest, format: 'xyz', role: 'reactant' }, limits: { max_rounds: 2, budget } };
const report = { schema: 'acp2.evidence.report.v1', report_id: 'report-a', project_id: task.project_id, research_id: input.research_id, round: 0, task_ref: task, status: 'complete', decision: 'accept', claims: [{ claim_id: 'claim-a', statement: 'Synthetic probe claim', status: 'supported', evidence_refs: ['unverified-string'], reason: null }], evidence_refs: ['unverified-string'], terminal_event_key: 'done-a' };
async function runCase(value, options = {}) {
  const log = { submits: [], waits: [] };
  const ports = {
    preflight: async () => ({ ok: true, reason: '', checks: [] }),
    admission: async () => ({ ok: true, reason: '', approval_ref: 'approval-a' }),
    approve: async () => ({ approved: true, approval_ref: 'approval-a', reason: '' }),
    submit: async request => { log.submits.push(request); return { ok: true, task_ref: task, reason: '' }; },
    wait: async request => { log.waits.push(request); return { cursor: 'cursor-next', events: options.emptyWait ? [] : [{ event_key: 'done-a', type: 'job.completed', task_ref: options.eventTask ?? task, report: value }] }; },
  };
  const result = await runSupervisor(input, ports);
  return { stop_reason: result.stop_reason, status: result.status, model_invocations: result.model_invocations, submits: result.submits, wait_requests: log.waits, plan: log.submits[0]?.plan, persisted_port_required: false };
}
const foreign = { ...task, project_id: 'other-project', job_id: 'other-job', attempt: 99, release_id: 'other-release' };
const science = JSON.parse(readFileSync(new URL('../../tests/fixtures/acp2/science/contracts/evidence/supported_valid.json', import.meta.url), 'utf8'));
let versionResult;
try { assertOperationAllowed('proposal.create', [{ id: 'acp.research.v1', version: '1.1.0' }]); versionResult = { accepted: true }; }
catch (error) { versionResult = { accepted: false, error: error.error }; }
const request = { task: task.job_id, attempt: 1, resource: 'result_manifest', generation: 1, project: task.project_id, explicit: true };
const results = {
  scope: 'real RPH helpers with controlled fake ports; no model, Controller or QC called',
  node: process.version,
  bridge_extension_version: BRIDGE_EXTENSION_VERSION,
  server_extension_compatibility: versionResult,
  bridge_arguments: Object.fromEntries(['prepare', 'read', 'evidence'].map(operation => [operation, buildToolArguments({ ...request, operation })])),
  science_report_rejected_by_rph: validateEvidenceReport(science),
  baseline: await runCase(report),
  foreign_report: await runCase({ ...report, project_id: 'other-project', research_id: 'other-research', task_ref: foreign }, { eventTask: foreign }),
  partial_report: await runCase({ ...report, status: 'partial' }),
  first_wait_empty: await runCase(report, { emptyWait: true }),
};
const frontendContext = vm.createContext({ console, Date, JSON, Uint8Array, TextEncoder });
frontendContext.window = frontendContext;
vm.runInContext(readFileSync(new URL('../../frontend/js/workbench/features/research-approval.js', import.meta.url), 'utf8'), frontendContext);
const approval = frontendContext.window.ACPResearchApproval;
approval.init({ now: () => Date.parse('2026-10-26T00:00:00Z') });
const proposal = JSON.parse(readFileSync(new URL('../../docs/reference/next-change/acp2-contracts/experience/fixtures/proposal.valid.json', import.meta.url), 'utf8'));
const principal = { kind: 'user', user_id: 'audit-human' };
const card = approval.requestApproval({ proposal, requestedBy: principal, approverRef: principal.user_id });
const approved = approval.decideApproval(card, { principal, decision: 'approve', expectedRevision: card.revision });
results.frontend_approval_without_platform = {
  scope: 'real exported UI functions in VM; no transport/persistence adapter injected',
  approval_ref: approved.approval_ref,
  status: approved.status,
  submit_result: approval.submitPlan(approved, proposal, { principal, boundDigest: approved.binding.plan_definition_digest }),
};
writeFileSync(new URL('./evidence/node-probes.json', import.meta.url), JSON.stringify(results, null, 2) + '\n');
console.log(JSON.stringify({ extensionAccepted: versionResult.accepted, scienceReportErrors: results.science_report_rejected_by_rph.length, foreignReport: results.foreign_report.stop_reason, partialReport: results.partial_report.stop_reason, wait: results.first_wait_empty.stop_reason }));
